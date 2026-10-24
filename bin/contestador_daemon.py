#!/usr/bin/env python3
"""
Contestador ligero para módem Conexant CX930xx (V.253) en /dev/ttyACM0.
Sin Asterisk / sin vgetty: responde, reproduce locución, graba y dispara el pipeline.
"""
from __future__ import annotations

import logging
import os
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import serial

DEVICE = os.environ.get("MODEM_DEVICE", "/dev/ttyACM0")
BAUD = int(os.environ.get("MODEM_BAUD", "115200"))
RINGS_TO_ANSWER = int(os.environ.get("RINGS_TO_ANSWER", "1"))
MAX_RECORD_SEC = int(os.environ.get("MAX_RECORD_SEC", "120"))
SILENCE_HANG_SEC = float(os.environ.get("SILENCE_HANG_SEC", "5"))
GREETING_WAV = Path(os.environ.get("GREETING_WAV", "/opt/contestador/media/greeting.wav"))
INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/var/spool/voice/incoming"))
ON_MESSAGE = Path(os.environ.get("ON_MESSAGE", "/opt/contestador/bin/on_message.sh"))
LOG_PATH = Path(os.environ.get("CONTESTADOR_LOG", "/var/log/contestador_daemon.log"))

DLE = 0x10
ETX = 0x03
DC4 = 0x14
NUL = 0x00

running = True


def setup_log() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stdout),
        ],
    )


def handle_signal(signum, frame):  # noqa: ANN001
    global running
    logging.info("Señal %s, saliendo…", signum)
    running = False


class Modem:
    def __init__(self, port: str, baud: int) -> None:
        self.ser = serial.Serial(port, baud, timeout=0.2, rtscts=True)
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def cmd(self, command: str, wait: float = 1.5, expect_ok: bool = True) -> str:
        self.ser.reset_input_buffer()
        self.write((command + "\r").encode("ascii", errors="ignore"))
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if any(x in buf for x in (b"OK", b"ERROR", b"CONNECT", b"VCON")):
                    # pequeña espera extra por restos
                    time.sleep(0.05)
                    buf += self.ser.read(self.ser.in_waiting or 0)
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("ascii", errors="ignore")
        logging.debug(">>> %s\n<<< %r", command, text)
        if expect_ok and "ERROR" in text and "OK" not in text:
            logging.warning("Modem ERROR en %s: %r", command, text)
        return text

    def read_line(self, timeout: float = 1.0) -> str:
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            ch = self.ser.read(1)
            if not ch:
                if buf:
                    break
                continue
            buf += ch
            if ch in (b"\n", b"\r") and buf.strip():
                break
        return buf.decode("ascii", errors="ignore").strip()

    def init(self) -> None:
        self.cmd("ATH", wait=2, expect_ok=False)
        self.cmd("ATZ", wait=3)
        self.cmd("ATE0")
        self.cmd("ATQ0V1")
        # Datos: RING ASCII fiable. Contestamos al timbre con S0 en voz.
        self.cmd("AT+FCLASS=0")
        self.cmd("ATS0=0")
        self.cmd("AT+VCID=1", expect_ok=False)
        logging.info("Módem inicializado en %s (espera RING)", DEVICE)

    def read_event(self, timeout: float = 1.0) -> tuple[str, str]:
        """Lee línea ASCII o evento DLE. Devuelve (tipo, texto).
        tipo: 'line' | 'ring' | 'vcon' | ''
        """
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end:
            n = self.ser.in_waiting
            chunk = self.ser.read(n or 1)
            if not chunk:
                if buf and (time.time() > end - 0.05):
                    break
                continue
            i = 0
            while i < len(chunk):
                b = chunk[i]
                # DLE-escaped voice event (p.ej. DLE R = ring en modo voz)
                if b == DLE and i + 1 < len(chunk):
                    code = chunk[i + 1]
                    i += 2
                    if code in (ord("R"), ord("r")):
                        return "ring", "DLE-R"
                    if code in (ord("w"), ord("W")):
                        return "vcon", "DLE-w"
                    continue
                buf.append(b)
                if b in (0x0A, 0x0D) and buf.strip():
                    text = bytes(buf).decode("ascii", errors="ignore").strip()
                    buf.clear()
                    if text:
                        return "line", text
                i += 1
        if buf.strip():
            text = bytes(buf).decode("ascii", errors="ignore").strip()
            if text:
                return "line", text
        return "", ""

    def select_line(self) -> None:
        """Enruta audio a la línea (ciclo que funcionó en llamada saliente)."""
        for vls in ("1", "5", "13", "4"):
            self.cmd(f"AT+VLS={vls}", wait=1.2, expect_ok=False)
        self.cmd("AT+VSM=1", expect_ok=False)
        self.cmd("AT+VSD=128,50", expect_ok=False)

    def hangup(self) -> None:
        try:
            self.write(bytes([DLE, ETX]))
            time.sleep(0.2)
        except Exception:
            pass
        self.cmd("ATH", wait=2, expect_ok=False)
        self.cmd("ATS0=0", expect_ok=False)
        self.cmd("AT+FCLASS=0", expect_ok=False)

    def wait_answered(self, timeout: float = 20.0) -> bool:
        """Espera VCON/CONNECT tras autoanswer (ATS0) o ATA."""
        end = time.time() + timeout
        while time.time() < end and running:
            et, tx = self.read_event(timeout=0.5)
            if et == "vcon":
                logging.info("Contestada (evento DLE)")
                self.select_line()
                return True
            if et == "line":
                u = tx.upper()
                if "VCON" in u or u.startswith("CONNECT"):
                    logging.info("Contestada (%s)", tx)
                    self.select_line()
                    return True
                if "NMBR" in u and "=" in tx:
                    logging.info("Caller-ID (mientras suena): %s", tx.split("=", 1)[1].strip())
                if "RING" in u:
                    logging.info("RING (autoanswer pendiente): %s", tx)
            elif et == "ring":
                logging.info("RING DLE (autoanswer pendiente)")
        return False

    def answer(self) -> bool:
        """Asegurar voz + S0 y esperar a que el módem descolgue solo."""
        leftover = self.ser.read(self.ser.in_waiting or 0)
        if leftover:
            logging.info("Buffer pre-answer: %r", leftover[:120])
        self.cmd("AT+FCLASS=8", expect_ok=False)
        self.cmd(f"ATS0={max(1, RINGS_TO_ANSWER)}", expect_ok=False)
        # Si ya está sonando, a veces hace falta un ATA justo en el ring
        if self.wait_answered(timeout=3.0):
            return True
        # Empujar ATA solo tras detectar ring en voz
        logging.info("Sin VCON aún — espero RING y pruebo ATA…")
        ring_deadline = time.time() + 12
        while time.time() < ring_deadline and running:
            et, tx = self.read_event(timeout=0.4)
            if et == "ring" or (et == "line" and "RING" in tx.upper()):
                logging.info("RING → ATA inmediato")
                self.write(b"ATA\r")
                end = time.time() + 8
                buf = b""
                while time.time() < end:
                    chunk = self.ser.read(256)
                    if chunk:
                        buf += chunk
                        if any(x in buf for x in (b"VCON", b"CONNECT", b"OK", b"ERROR")):
                            time.sleep(0.1)
                            buf += self.ser.read(self.ser.in_waiting or 0)
                            break
                    else:
                        time.sleep(0.02)
                r = buf.decode("ascii", errors="ignore")
                logging.info("ATA → %r", r.replace("\r", "\\r").replace("\n", "\\n")[:100])
                if "VCON" in r.upper() or "CONNECT" in r.upper():
                    self.select_line()
                    return True
                # OK solo sin ERROR también vale en algunos firmware
                if "OK" in r.upper() and "ERROR" not in r.upper():
                    self.select_line()
                    return True
            if et == "vcon" or (et == "line" and ("VCON" in tx.upper() or tx.upper().startswith("CONNECT"))):
                self.select_line()
                return True
        logging.error("No hubo VCON ni ATA OK")
        return False

    def _vtx_send(self, pcm: bytes) -> bool:
        """Envía PCM u8@8kHz con ritmo casi tiempo real. True si CONNECT/OK."""
        r = self.cmd("AT+VTX", wait=4, expect_ok=False)
        u = r.upper()
        if "ERROR" in u and "CONNECT" not in u and "OK" not in u:
            logging.warning("AT+VTX rechazado: %r", r.replace("\r", "\\r").replace("\n", "\\n")[:80])
            return False
        time.sleep(0.2)
        payload = shield_dle(pcm)
        chunk = 512
        for i in range(0, len(payload), chunk):
            part = payload[i : i + chunk]
            self.write(part)
            # 8000 bytes/s → ritmo ~85% tiempo real (margen buffer módem)
            time.sleep(len(part) / 8000.0 * 0.85)
        self.write(bytes([DLE, ETX]))
        end = time.time() + 12
        while time.time() < end:
            t = self.ser.read(256)
            if b"OK" in t:
                return True
            if b"ERROR" in t:
                logging.warning("VTX fin ERROR: %r", t[:60])
                return False
            time.sleep(0.05)
        return True

    def play_wav_pcm_u8(self, wav_path: Path) -> None:
        """Reproduce WAV por la línea (AT+VTX)."""
        raw = wav_to_u8_8k(wav_path)
        self.select_line()
        if self._vtx_send(raw):
            logging.info("Locución reproducida (%d bytes PCM)", len(raw))
            return
        logging.warning("VTX falló, reintento ciclando VLS…")
        for vls in ("1", "0", "5", "13", "4"):
            self.cmd(f"AT+VLS={vls}", wait=2, expect_ok=False)
            self.cmd("AT+VSM=1", expect_ok=False)
            if self._vtx_send(raw):
                logging.info("Locución OK con VLS=%s (%d bytes)", vls, len(raw))
                return
        # Al menos beep DTMF/tone audible
        self.cmd("AT+VTS=[933,0,1500]", wait=5, expect_ok=False)
        logging.error("No se pudo reproducir locución PCM")

    def beep(self) -> None:
        r = self.cmd("AT+VTS=[933,0,1000]", wait=3, expect_ok=False)
        if "ERROR" in r.upper() and "OK" not in r.upper():
            beep = synth_beep_u8(933, 1.0)
            self.select_line()
            self._vtx_send(beep)

    def record(self, out_wav: Path, max_sec: float = MAX_RECORD_SEC) -> int:
        r = self.cmd("AT+VRX", wait=3, expect_ok=False)
        if "ERROR" in r.upper() and "CONNECT" not in r.upper() and "OK" not in r.upper():
            logging.warning("VRX no disponible (%r) — no grabo para no bloquear", r[:60])
            write_u8_wav(out_wav, b"")
            return 0
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        data = bytearray()
        start = time.time()
        last_loud = start
        got_audio_at = None
        while time.time() - start < max_sec and running:
            # Si en 6s no llega audio, abortar (evita bloqueo tras ATA/ATH1 fallido)
            if got_audio_at is None and time.time() - start > 6:
                logging.warning("VRX sin audio en 6s — abortando grabación")
                try:
                    self.write(bytes([DLE, ETX]))
                except Exception:
                    pass
                write_u8_wav(out_wav, bytes(data))
                return len(data)
            chunk = self.ser.read(2048)
            if not chunk:
                time.sleep(0.01)
                continue
            if got_audio_at is None:
                got_audio_at = time.time()
            # Detectar fin DLE ETX / DLE DC4 (silence?)/ hangup
            i = 0
            plain = bytearray()
            while i < len(chunk):
                b = chunk[i]
                if b == DLE and i + 1 < len(chunk):
                    n = chunk[i + 1]
                    if n == DLE:
                        plain.append(DLE)
                        i += 2
                        continue
                    if n == ETX:
                        # fin de stream
                        i = len(chunk)
                        write_u8_wav(out_wav, bytes(data + plain))
                        logging.info("Fin VRX (ETX), %d bytes", len(data) + len(plain))
                        return len(data) + len(plain)
                    if n in (0x62, 0x42):  # 'b'/'B' busy?
                        i += 2
                        continue
                    if n in (0x73, 0x53):  # silence 's'/'S'
                        i += 2
                        if time.time() - last_loud > SILENCE_HANG_SEC and len(data) > 4000:
                            self.write(bytes([DLE, ETX]))
                            time.sleep(0.2)
                            write_u8_wav(out_wav, bytes(data + plain))
                            logging.info("Silencio prolongado, fin grabación")
                            return len(data) + len(plain)
                        continue
                    # otro DLE-code: ignorar
                    i += 2
                    continue
                plain.append(b)
                # crude energy
                if abs(b - 128) > 12:
                    last_loud = time.time()
                i += 1
            data.extend(plain)
        self.write(bytes([DLE, ETX]))
        time.sleep(0.3)
        write_u8_wav(out_wav, bytes(data))
        logging.info("Timeout grabación, %d bytes", len(data))
        return len(data)


def shield_dle(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


def wav_to_u8_8k(path: Path) -> bytes:
    """Devuelve PCM unsigned 8-bit mono 8 kHz."""
    tmp = Path("/tmp/contestador_play.wav")
    subprocess.check_call(
        ["sox", str(path), "-r", "8000", "-c", "1", "-b", "8", "-e", "unsigned-integer", str(tmp)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with wave.open(str(tmp), "rb") as w:
        return w.readframes(w.getnframes())


def write_u8_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(pcm)


def synth_beep_u8(freq: float, seconds: float, rate: int = 8000) -> bytes:
    import math

    n = int(rate * seconds)
    out = bytearray(n)
    for i in range(n):
        # unsigned 8-bit centered at 128
        val = 128 + int(60 * math.sin(2 * math.pi * freq * i / rate))
        out[i] = max(0, min(255, val))
    return bytes(out)


def ensure_greeting() -> None:
    if GREETING_WAV.is_file() and GREETING_WAV.stat().st_size > 0:
        return
    GREETING_WAV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "espeak-ng",
            "-v",
            "es",
            "-s",
            "140",
            "-w",
            str(GREETING_WAV),
            "Hola. Deja tu mensaje después de la señal. Gracias.",
        ]
    )
    logging.info("Generada locución %s", GREETING_WAV)


def normalize_caller(raw: str) -> str:
    u = (raw or "").strip().upper()
    if not u or u in {"P", "O", "PRIVATE", "OUT_OF_AREA", "UNAVAILABLE", "UNAVALIABLE"}:
        return "número oculto"
    return raw.strip()


def load_called_id() -> str:
    """Número de esta línea (Called-ID), desde env o /etc/contestador/email.env."""
    for key in ("CALLED_ID", "LINE_NUMBER"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    env_path = Path("/etc/contestador/email.env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() in {"CALLED_ID", "LINE_NUMBER"}:
                val = val.strip().strip('"').strip("'")
                if val:
                    return val
    return ""


def process_message(wav: Path, caller: str) -> None:
    env = os.environ.copy()
    env["CALLER_ID"] = normalize_caller(caller)
    env["CALLED_ID"] = load_called_id()
    env["DEVICE"] = Path(DEVICE).name
    logging.info(
        "Pipeline mensaje caller=%s called=%s file=%s",
        env["CALLER_ID"],
        env["CALLED_ID"] or "(vacío)",
        wav,
    )
    try:
        # on_message.sh acepta WAV directamente
        subprocess.check_call([str(ON_MESSAGE), str(wav)], env=env)
    except Exception as e:
        logging.exception("Pipeline falló: %s", e)


def seize_and_play(modem: Modem, caller: str) -> None:
    """Al RING: pasar a voz, forzar descolgado (S0) y reproducir sí o sí."""
    logging.info("RING → descolgando y reproduciendo (caller=%s)", caller or "?")
    modem.cmd("AT+FCLASS=8", expect_ok=False)
    modem.cmd(f"ATS0={max(1, RINGS_TO_ANSWER)}", expect_ok=False)
    # Dar tiempo al hardware a descolgar (aunque no llegue VCON)
    t_end = time.time() + 2.5
    while time.time() < t_end:
        et, tx = modem.read_event(timeout=0.3)
        if et == "line" and "NMBR" in tx.upper() and "=" in tx:
            caller = tx.split("=", 1)[1].strip()
            logging.info("Caller-ID: %s", caller)
        if et == "vcon" or (et == "line" and ("VCON" in tx.upper() or tx.upper().startswith("CONNECT"))):
            logging.info("VCON recibido")
            break
    # Vaciar basura y forzar path de audio + locución
    junk = modem.ser.read(modem.ser.in_waiting or 0)
    if junk:
        logging.info("Buffer post-descolgado: %r", junk[:80])
    try:
        modem.select_line()
        # Beep primero (confirma que la línea lleva audio)
        modem.cmd("AT+VTS=[933,0,800]", wait=3, expect_ok=False)
        modem.play_wav_pcm_u8(GREETING_WAV)
        modem.beep()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        wav = INCOMING_DIR / f"msg_{stamp}.wav"
        n = modem.record(wav)
        modem.hangup()
        if n > 1000:
            logging.info("Mensaje guardado %s (%d bytes)", wav, n)
            process_message(wav, caller)
        else:
            logging.warning("Grabación corta/vacía (%d) — igual colgamos limpio", n)
    except Exception:
        logging.exception("Error en llamada")
        modem.hangup()
    time.sleep(1)
    modem.init()


def main() -> int:
    setup_log()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    # Regenerar locución con el texto actual
    subprocess.call(["/opt/contestador/bin/make_greeting_wav.sh"])
    ensure_greeting()
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    modem = Modem(DEVICE, BAUD)
    try:
        modem.init()
        caller = ""
        logging.info("Esperando llamadas (contesta al primer RING + locución)…")
        while running:
            etype, text = modem.read_event(timeout=1.0)
            if not etype:
                continue

            if etype == "vcon" or (
                etype == "line" and ("VCON" in text.upper() or text.upper().startswith("CONNECT"))
            ):
                logging.info("VCON espontáneo → locución")
                seize_and_play(modem, caller)
                caller = ""
                continue

            if etype == "ring" or (etype == "line" and "RING" in text.upper()):
                logging.info("RING detectado (%s)", text or etype)
                # Recoger CID un instante si llega pegado al RING
                cid_until = time.time() + 0.4
                while time.time() < cid_until:
                    e2, t2 = modem.read_event(timeout=0.15)
                    if e2 == "line" and "NMBR" in t2.upper() and "=" in t2:
                        caller = t2.split("=", 1)[1].strip()
                        logging.info("Caller-ID: %s", caller)
                seize_and_play(modem, caller)
                caller = ""
                continue

            if etype == "line":
                u = text.upper()
                if "NMBR" in u and "=" in text:
                    caller = text.split("=", 1)[1].strip()
                    logging.info("Caller-ID: %s", caller)
                elif u not in {"OK"} and not any(
                    u.startswith(p) for p in ("DATE", "TIME", "NAME")
                ):
                    logging.info("Modem: %s", text)
    finally:
        modem.hangup()
        modem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
