#!/usr/bin/env python3
"""
Buzonera — contestador telefónico para módem Conexant CX930xx (V.253).
Responde, reproduce locución, graba y dispara el pipeline de correo.
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
MAX_RECORD_SEC = int(os.environ.get("MAX_RECORD_SEC", "90"))
# Segundos de silencio / tono de ocupado tras colgar antes de cortar la grabación
SILENCE_HANG_SEC = float(os.environ.get("SILENCE_HANG_SEC", "2.0"))
# |sample-128| medio por ventana para considerar "silencio" (u8 PCM)
SILENCE_RMS_THRESH = float(os.environ.get("SILENCE_RMS_THRESH", "10"))
# No cortar por silencio antes de haber grabado al menos esto
MIN_MESSAGE_SEC = float(os.environ.get("MIN_MESSAGE_SEC", "1.0"))
GREETING_WAV = Path(os.environ.get("GREETING_WAV", "/opt/buzonera/media/greeting.wav"))
INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/var/spool/voice/incoming"))
ON_MESSAGE = Path(os.environ.get("ON_MESSAGE", "/opt/buzonera/bin/on_message.sh"))
LOG_PATH = Path(os.environ.get("BUZONERA_LOG", "/var/log/buzonera_daemon.log"))

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


def looks_like_at_response(buf: bytes) -> bool:
    """True si el buffer parece respuesta AT (no PCM de voz)."""
    if not buf:
        return False
    if any(x in buf for x in (b"OK", b"ERROR", b"CONNECT", b"VCON", b"RING")):
        return True
    # PCM u8 suele centrarse en 0x7f/0x80; AT es ASCII imprimible corto
    printable = sum(1 for b in buf if 32 <= b < 127 or b in (10, 13))
    return printable / max(1, len(buf)) > 0.85 and len(buf) < 400


def usb_reset_acm(device: str = DEVICE) -> bool:
    """Quita/pone authorized del USB del ttyACM (último recurso)."""
    name = Path(device).name  # ttyACM0
    sys_dev = Path(f"/sys/class/tty/{name}/device")
    try:
        iface = sys_dev.resolve()
        usb = iface.parent  # dispositivo USB
        auth = usb / "authorized"
        if not auth.is_file():
            return False
        logging.warning("USB reset de %s vía %s", device, auth)
        auth.write_text("0")
        time.sleep(0.8)
        auth.write_text("1")
        # Esperar a que reaparezca el nodo
        for _ in range(40):
            if Path(device).exists():
                time.sleep(0.4)
                return True
            time.sleep(0.25)
    except Exception as e:
        logging.warning("USB reset falló: %s", e)
    return False


class Modem:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        # Este Conexant CDC-ACM necesita RTS/CTS para responder OK a los AT
        self.ser = serial.Serial(port, baud, timeout=0.2, rtscts=True)
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self._pending_dle = False  # DLE solo al final de un chunk

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def _drain(self, seconds: float = 1.0) -> int:
        t0 = time.time()
        n = 0
        while time.time() - t0 < seconds:
            chunk = self.ser.read(8192)
            if chunk:
                n += len(chunk)
                continue
            # un poco más de silencio → listo
            if time.time() - t0 > 0.15 and n:
                break
            time.sleep(0.02)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        return n

    def reopen(self) -> None:
        """Cierra y reabre el puerto (sale de streams PCM atascados)."""
        logging.info("Reabriendo puerto serie %s…", self.port)
        try:
            self.ser.close()
        except Exception:
            pass
        time.sleep(0.6)
        for _ in range(20):
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=0.2, rtscts=True)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError(f"No se pudo reabrir {self.port}")
        time.sleep(0.35)
        self._pending_dle = False
        self.ser.reset_input_buffer()

    def probe_at(self, wait: float = 1.2) -> bool:
        """Manda AT y exige un OK limpio."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            return False
        self.write(b"AT\r")
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b"OK" in buf and looks_like_at_response(buf):
                    return True
                # Si llega PCM, abortar pronto
                if len(buf) > 200 and not looks_like_at_response(buf):
                    return False
            else:
                time.sleep(0.02)
        return b"OK" in buf and looks_like_at_response(buf)

    def force_recover(self) -> None:
        """Sale de VRX/VTX atascado y vuelve a modo datos (RING ASCII).

        El CX93010 tras colgar a menudo sigue tirando PCM: los AT no sirven
        hasta reabrir el puerto (o reset USB).
        """
        logging.info("Recuperando módem tras llamada…")
        for _ in range(6):
            try:
                self.write(bytes([DLE, ETX]))
                self.ser.flush()
            except Exception:
                pass
            time.sleep(0.06)
        drained = self._drain(1.2)
        if drained:
            logging.info("Drenados %d bytes PCM residual", drained)

        # Toggle DTR (cuelga en muchos módems)
        try:
            self.ser.dtr = False
            time.sleep(0.25)
            self.ser.dtr = True
            time.sleep(0.4)
        except Exception:
            pass
        self._drain(0.5)

        if self.probe_at():
            logging.info("Módem OK tras DLE/DTR")
        else:
            try:
                self.reopen()
            except Exception as e:
                logging.warning("Reopen falló: %s — USB reset", e)
                self.close()
                usb_reset_acm(self.port)
                time.sleep(1.0)
                self.reopen()
            if not self.probe_at():
                logging.warning("Tras reopen sin OK — USB reset")
                self.close()
                if usb_reset_acm(self.port):
                    time.sleep(1.0)
                    self.reopen()
                if not self.probe_at(wait=2.0):
                    logging.error("Módem sin respuesta AT tras recover")

        # Secuencia limpia data mode
        for c, w in (
            ("ATH", 1.5),
            ("ATZ", 3.0),
            ("ATE0", 1.0),
            ("ATQ0V1", 1.0),
            ("AT+GCI=2E", 1.0),
            ("AT+FCLASS=0", 1.5),
            ("ATS0=0", 1.0),
            ("AT+VCID=1", 1.0),
        ):
            self.cmd(c, wait=w, expect_ok=False)
        fc = self.cmd("AT+FCLASS?", wait=1.0, expect_ok=False)
        self._drain(0.2)
        if "OK" not in fc.upper() or "0" not in fc:
            logging.warning("FCLASS dudoso tras recover (%r) — reintento", fc[:60])
            self.cmd("ATZ", wait=3, expect_ok=False)
            self.cmd("AT+FCLASS=0", expect_ok=False)
            fc = self.cmd("AT+FCLASS?", wait=1.0, expect_ok=False)
        logging.info("Módem listo (recover) — FCLASS?=%r", fc.strip()[:40])

    def cmd(self, command: str, wait: float = 1.5, expect_ok: bool = True) -> str:
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.write((command + "\r").encode("ascii", errors="ignore"))
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if any(x in buf for x in (b"OK", b"ERROR", b"CONNECT", b"VCON")):
                    # Exigir que no sea PCM disfrazado
                    if looks_like_at_response(buf) or len(buf) < 80:
                        time.sleep(0.05)
                        buf += self.ser.read(self.ser.in_waiting or 0)
                        break
                if len(buf) > 800 and not looks_like_at_response(buf):
                    # Puerto inundado de PCM
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("ascii", errors="ignore")
        show = text.replace("\r", "\\r").replace("\n", "\\n")
        if not looks_like_at_response(buf) and len(buf) > 40:
            logging.info("AT >>> %s <<< <PCM/ruido %d bytes>", command, len(buf))
        else:
            logging.info("AT >>> %s <<< %r", command, show[:120])
        if expect_ok and "ERROR" in text.upper() and "OK" not in text.upper():
            logging.warning("Modem ERROR en %s", command)
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
        # Arranque / post-llamada: si no hay AT limpio, recover completo
        for _ in range(3):
            try:
                self.write(bytes([DLE, ETX]))
                self.ser.flush()
            except Exception:
                pass
            time.sleep(0.08)
        self._drain(0.6)
        if not self.probe_at():
            self.force_recover()
            return
        self.cmd("ATH", wait=1.5, expect_ok=False)
        self.cmd("ATZ", wait=3, expect_ok=False)
        self.cmd("ATE0", expect_ok=False)
        self.cmd("ATQ0V1", expect_ok=False)
        self.cmd("AT+GCI=2E", expect_ok=False)  # España; si ERROR se ignora
        self.cmd("AT+FCLASS=0", expect_ok=False)
        fc = self.cmd("AT+FCLASS?", wait=1.0, expect_ok=False)
        self.cmd("ATS0=0", expect_ok=False)
        self.cmd("AT+VCID=1", expect_ok=False)
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        if "0" not in fc or "OK" not in fc.upper():
            logging.warning("FCLASS no limpio (%r) — force_recover", fc[:60])
            self.force_recover()
            return
        logging.info("Módem listo en %s — FCLASS?=%r", DEVICE, fc.strip()[:40])

    def answer_now(self) -> None:
        """Descolgar en el pulso de timbre (secuencia Conexant TAD).

        Importante: NO mandar ATH1 antes. En este chip ATH1 no cierra el lazo
        PSTN y además retrasa/rompe el VLS=1. CallAttendant usa solo:
          AT+FCLASS=8  →  AT+VLS=1
        """
        logging.info("Descolgando YA (FCLASS=8 + VLS=1)")
        # Disparo crudo en <100 ms (sin esperar OK)
        self.write(b"AT+FCLASS=8\r")
        self.ser.flush()
        time.sleep(0.08)
        self.write(b"AT+VLS=1\r")
        self.ser.flush()
        time.sleep(0.35)
        # Reforzar path voz + línea (por si el crudo se perdió)
        self.cmd("AT+FCLASS=8", wait=0.8, expect_ok=False)
        self.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
        r = self.cmd("AT+VLS=1", wait=1.0, expect_ok=False)
        if "ERROR" in r.upper():
            logging.warning("VLS=1 ERROR — pruebo VLS=5 y ATA")
            self.cmd("AT+VLS=5", wait=1.0, expect_ok=False)
            self.write(b"ATA\r")
            self.ser.flush()
            time.sleep(0.3)
        self.ser.reset_input_buffer()

    def _dle_event(self, code: int) -> tuple[str, str] | None:
        """Interpreta código tras DLE. Conexant manda DLE-a en cada timbre."""
        # RING: R/r = V.253. En este CX93010 el timbre llega como DLE-a
        # (aunque en el estándar 'a' sea answer-tone; aquí coincide con el cadence).
        if code in (ord("R"), ord("r"), ord("a"), ord("A")):
            return "ring", f"DLE-{chr(code)}"
        # Contestada / connect voice
        if code in (ord("w"), ord("W"), ord("c"), ord("C")):
            return "vcon", f"DLE-{chr(code)}"
        # Hangup remoto
        if code in (ord("h"), ord("H")):
            return "line", "NO CARRIER"
        return None

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
                if self._pending_dle:
                    self._pending_dle = False
                    ev = self._dle_event(b)
                    i += 1
                    if ev:
                        return ev
                    continue
                if b == DLE:
                    if i + 1 < len(chunk):
                        code = chunk[i + 1]
                        i += 2
                        ev = self._dle_event(code)
                        if ev:
                            return ev
                        continue
                    # DLE solo: esperar el siguiente byte
                    self._pending_dle = True
                    i += 1
                    continue
                buf.append(b)
                if b in (0x0A, 0x0D) and buf.strip():
                    # Quitar DLEs sueltos del texto
                    raw = bytes(x for x in buf if x != DLE)
                    buf.clear()
                    text = raw.decode("ascii", errors="ignore").strip()
                    if text:
                        if "RING" in text.upper():
                            return "ring", text
                        return "line", text
                i += 1
        if buf.strip():
            raw = bytes(x for x in buf if x != DLE)
            text = raw.decode("ascii", errors="ignore").strip()
            if text:
                if "RING" in text.upper():
                    return "ring", text
                return "line", text
        return "", ""

    def select_line(self) -> None:
        """Enruta audio a la línea telefónica (VLS=1 = T). Rápido."""
        self.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
        self.cmd("AT+VSD=0,40", expect_ok=False)
        r = self.cmd("AT+VLS=1", wait=1.2, expect_ok=False)
        if "ERROR" in r.upper() and "OK" not in r.upper():
            logging.warning("VLS=1 falló (%r), pruebo VLS=5", r[:40])
            self.cmd("AT+VLS=5", wait=1.2, expect_ok=False)
        else:
            logging.info("Audio enrutado a línea (VLS=1)")

    def hangup(self) -> None:
        # Intento rápido de colgar; si el chip sigue en PCM, force_recover lo arregla
        for _ in range(4):
            try:
                self.write(bytes([DLE, ETX]))
                self.ser.flush()
            except Exception:
                pass
            time.sleep(0.08)
        self._drain(0.8)
        # Solo mandar AT si el puerto ya responde (si no, no perder 15 s en ruido)
        if self.probe_at(wait=0.8):
            self.cmd("AT+VLS=0", wait=1.0, expect_ok=False)
            self.cmd("ATH", wait=1.5, expect_ok=False)
            self.cmd("AT+FCLASS=0", wait=1.5, expect_ok=False)
        self._drain(0.2)

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
        """Envía PCM u8@8kHz a ritmo tiempo real (reloj absoluto).

        En Pi Zero el sleep por trozo acumula jitter y *0.85 inundaba el
        buffer del módem → audio entrecortado.
        """
        r = self.cmd("AT+VTX", wait=4, expect_ok=False)
        u = r.upper()
        if "ERROR" in u and "CONNECT" not in u and "OK" not in u:
            logging.warning("AT+VTX rechazado: %r", r.replace("\r", "\\r").replace("\n", "\\n")[:80])
            return False
        time.sleep(0.25)
        payload = shield_dle(pcm)
        chunk = 1024  # menos syscalls en el Zero
        rate = 8000.0 / 1.02  # ligeramente más lento que 8 kHz → sin underrun
        t0 = time.monotonic()
        sent = 0
        for i in range(0, len(payload), chunk):
            part = payload[i : i + chunk]
            self.write(part)
            sent += len(part)
            delay = (t0 + sent / rate) - time.monotonic()
            if delay > 0.002:
                time.sleep(delay)
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
        # Asegurar path a línea justo antes de transmitir
        self.cmd("AT+VLS=1", wait=1.5, expect_ok=False)
        self.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
        if self._vtx_send(raw):
            logging.info("Locución reproducida (%d bytes PCM)", len(raw))
            return
        logging.warning("VTX falló, reintento VLS=1 / VLS=5…")
        for vls in ("1", "5"):
            self.cmd(f"AT+VLS={vls}", wait=2, expect_ok=False)
            self.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
            if self._vtx_send(raw):
                logging.info("Locución OK con VLS=%s (%d bytes)", vls, len(raw))
                return
        # Al menos beep DTMF/tone audible en línea
        self.cmd("AT+VLS=1", wait=1.5, expect_ok=False)
        self.cmd("AT+VTS=[933,0,1500]", wait=5, expect_ok=False)
        logging.error("No se pudo reproducir locución PCM")

    def beep(self) -> None:
        self.cmd("AT+VLS=1", wait=1.0, expect_ok=False)
        r = self.cmd("AT+VTS=[933,0,1000]", wait=3, expect_ok=False)
        if "ERROR" in r.upper() and "OK" not in r.upper():
            beep = synth_beep_u8(933, 1.0)
            self.cmd("AT+VLS=1", wait=1.0, expect_ok=False)
            self._vtx_send(beep)

    def _finish_record(self, out_wav: Path, data: bytes, reason: str) -> int:
        """Cierra VRX, recorta silencio final y guarda WAV."""
        try:
            self.write(bytes([DLE, ETX]))
        except Exception:
            pass
        time.sleep(0.15)
        try:
            self.ser.read(self.ser.in_waiting or 0)
        except Exception:
            pass
        trimmed = trim_u8_pcm(data)
        write_u8_wav(out_wav, trimmed)
        sec = len(trimmed) / 8000.0
        logging.info(
            "Fin grabación (%s): %d → %d bytes (%.1fs)",
            reason,
            len(data),
            len(trimmed),
            sec,
        )
        return len(trimmed)

    def record(self, out_wav: Path, max_sec: float = MAX_RECORD_SEC) -> int:
        # Silencio más agresivo en el módem (Conexant: AT+VSD=sens,interval)
        # interval en 0.1 s → 15 = 1.5 s
        self.cmd("AT+VSD=0,15", expect_ok=False)
        r = self.cmd("AT+VRX", wait=3, expect_ok=False)
        if "ERROR" in r.upper() and "CONNECT" not in r.upper() and "OK" not in r.upper():
            logging.warning("VRX no disponible (%r) — no grabo para no bloquear", r[:60])
            write_u8_wav(out_wav, b"")
            return 0
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        data = bytearray()
        start = time.time()
        got_audio_at = None
        quiet_run = 0.0  # segundos consecutivos “en silencio”
        window = bytearray()
        win_sec = 0.2  # analizar energía cada 200 ms
        win_bytes = int(8000 * win_sec)
        pending_dle = False

        def handle_dle_code(n: int, plain: bytearray) -> int | None:
            """Procesa código tras DLE. Devuelve bytes finales o None para seguir."""
            nonlocal quiet_run
            if n == DLE:
                plain.append(DLE)
                return None
            if n == ETX:
                data.extend(plain)
                return self._finish_record(out_wav, bytes(data), "ETX")
            if n in (ord("h"), ord("H")):
                data.extend(plain)
                logging.info("DLE-h (colgado remoto)")
                return self._finish_record(out_wav, bytes(data), "colgado")
            if n in (ord("b"), ord("B")):
                data.extend(plain)
                logging.info("DLE-b (busy/tono)")
                return self._finish_record(out_wav, bytes(data), "busy")
            if n in (ord("s"), ord("S"), ord("q"), ord("Q")):
                quiet_run += 0.15
                if (
                    quiet_run >= SILENCE_HANG_SEC
                    and (time.time() - start) >= MIN_MESSAGE_SEC
                    and len(data) > 2000
                ):
                    data.extend(plain)
                    return self._finish_record(out_wav, bytes(data), "silencio-DLE")
            return None

        while time.time() - start < max_sec and running:
            if got_audio_at is None and time.time() - start > 6:
                logging.warning("VRX sin audio en 6s — abortando grabación")
                return self._finish_record(out_wav, bytes(data), "sin-audio")

            chunk = self.ser.read(2048)
            if not chunk:
                time.sleep(0.01)
                continue
            if got_audio_at is None:
                got_audio_at = time.time()

            plain = bytearray()
            i = 0
            while i < len(chunk):
                b = chunk[i]
                if pending_dle:
                    pending_dle = False
                    done = handle_dle_code(b, plain)
                    i += 1
                    if done is not None:
                        return done
                    continue
                if b == DLE:
                    if i + 1 < len(chunk):
                        done = handle_dle_code(chunk[i + 1], plain)
                        i += 2
                        if done is not None:
                            return done
                        continue
                    pending_dle = True
                    i += 1
                    continue
                plain.append(b)
                i += 1

            # Energía por ventanas (silencio real o tono estable tras colgar)
            window.extend(plain)
            data.extend(plain)
            while len(window) >= win_bytes:
                w = window[:win_bytes]
                del window[:win_bytes]
                energy = sum(abs(x - 128) for x in w) / float(win_bytes)
                mean = sum(w) / float(win_bytes)
                var = sum((x - mean) ** 2 for x in w) / float(win_bytes)
                is_quiet = energy < SILENCE_RMS_THRESH
                is_dialtone = energy > 18 and var < 80  # seno estable
                if is_quiet or is_dialtone:
                    quiet_run += win_sec
                else:
                    quiet_run = 0.0
                if (
                    quiet_run >= SILENCE_HANG_SEC
                    and (time.time() - start) >= MIN_MESSAGE_SEC
                    and len(data) > 2000
                ):
                    why = "tono-marcacion" if is_dialtone else "silencio"
                    return self._finish_record(out_wav, bytes(data), why)

        return self._finish_record(out_wav, bytes(data), "timeout")


def shield_dle(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


def wav_to_u8_8k(path: Path) -> bytes:
    """Devuelve PCM unsigned 8-bit mono 8 kHz (un poco más alto para línea telefónica)."""
    tmp = Path("/tmp/buzonera_play.wav")
    # vol +6 dB: la línea/módem atenúan; evitar locución inaudible
    subprocess.check_call(
        [
            "sox",
            str(path),
            "-r",
            "8000",
            "-c",
            "1",
            "-b",
            "8",
            "-e",
            "unsigned-integer",
            str(tmp),
            "vol",
            "6",
            "dB",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with wave.open(str(tmp), "rb") as w:
        return w.readframes(w.getnframes())


def trim_u8_pcm(pcm: bytes, thresh: int = 10, pad_ms: int = 150) -> bytes:
    """Recorta silencio al inicio/final del PCM u8 (centro 128)."""
    if not pcm:
        return pcm
    thr = thresh
    first = 0
    last = len(pcm) - 1
    while first < len(pcm) and abs(pcm[first] - 128) <= thr:
        first += 1
    while last > first and abs(pcm[last] - 128) <= thr:
        last -= 1
    pad = int(8000 * pad_ms / 1000)
    first = max(0, first - pad)
    last = min(len(pcm) - 1, last + pad)
    # También recortar tono de marcación final: bloque final muy estable
    # (si los últimos 1.5 s tienen poca varianza, cortarlos)
    tail = int(8000 * 1.5)
    if last - first > tail + 8000:
        region = pcm[last - tail + 1 : last + 1]
        mean = sum(region) / len(region)
        var = sum((x - mean) ** 2 for x in region) / len(region)
        energy = sum(abs(x - 128) for x in region) / len(region)
        if energy > 15 and var < 80:
            last = last - tail
    return bytes(pcm[first : last + 1])


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
    """Número de esta línea (Called-ID), desde env o /etc/buzonera/email.env."""
    for key in ("CALLED_ID", "LINE_NUMBER"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    env_path = Path("/etc/buzonera/email.env")
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
    """Lanza correo/transcripción en segundo plano para no bloquear el módem."""
    env = os.environ.copy()
    env["CALLER_ID"] = normalize_caller(caller)
    env["CALLED_ID"] = load_called_id()
    env["DEVICE"] = Path(DEVICE).name
    logging.info(
        "Pipeline mensaje (async) caller=%s called=%s file=%s",
        env["CALLER_ID"],
        env["CALLED_ID"] or "(vacío)",
        wav,
    )
    try:
        # No esperar: si el SMTP tarda, el daemon debe seguir escuchando RING
        subprocess.Popen(
            [str(ON_MESSAGE), str(wav)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logging.exception("Pipeline falló al lanzar: %s", e)


def seize_and_play(modem: Modem, caller: str, already_answered: bool = False) -> None:
    """Descolgar al instante, locución y grabar."""
    logging.info("Atendiendo YA (caller=%s)", caller or "?")
    # Tirar basura pero NO esperar — el timbre sigue sonando
    try:
        modem.ser.reset_input_buffer()
    except Exception:
        pass

    try:
        if not already_answered:
            modem.answer_now()
        else:
            modem.cmd("AT+FCLASS=8", wait=1.0, expect_ok=False)
            modem.select_line()

        time.sleep(0.25)
        modem.cmd("AT+VLS=1", wait=0.8, expect_ok=False)
        # Beep corto prueba de línea
        modem.cmd("AT+VTS=[933,0,400]", wait=1.5, expect_ok=False)
        modem.play_wav_pcm_u8(GREETING_WAV)
        modem.beep()
        modem.cmd("AT+VLS=1", wait=0.8, expect_ok=False)
        modem.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        wav = INCOMING_DIR / f"msg_{stamp}.wav"
        n = modem.record(wav, max_sec=MAX_RECORD_SEC)
        modem.hangup()
        # Crítico: sin recover el CX93010 sigue en PCM y la siguiente llamada no contesta
        modem.force_recover()
        if n > 1000:
            logging.info("Mensaje guardado %s (%d bytes)", wav, n)
            process_message(wav, caller)
        else:
            logging.warning("Grabación corta/vacía (%d) — sin pipeline", n)
            try:
                wav.unlink(missing_ok=True)
            except Exception:
                pass
        logging.info("Listo para siguiente llamada")
        return
    except Exception:
        logging.exception("Error en llamada")
        try:
            modem.hangup()
        except Exception:
            pass
        try:
            modem.force_recover()
        except Exception:
            logging.exception("Recover tras error falló")
        logging.info("Listo para siguiente llamada")


def main() -> int:
    setup_log()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    # Regenerar locución con el texto actual
    subprocess.call(["/opt/buzonera/bin/make_greeting_wav.sh"])
    ensure_greeting()
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    modem = Modem(DEVICE, BAUD)
    try:
        modem.init()
        caller = ""
        logging.info("Esperando llamadas (contesta al primer RING)…")
        noise_hits = 0
        while running:
            etype, text = modem.read_event(timeout=1.0)
            if not etype:
                noise_hits = 0
                continue

            if etype == "vcon" or (
                etype == "line" and ("VCON" in text.upper() or text.upper().startswith("CONNECT"))
            ):
                logging.info("VCON → locución")
                seize_and_play(modem, caller, already_answered=True)
                caller = ""
                noise_hits = 0
                continue

            if etype == "ring" or (etype == "line" and "RING" in text.upper()):
                logging.info("RING detectado (%s) → descolgar YA", text or etype)
                # No esperar CID: hay que enganchar en este pulso de timbre
                seize_and_play(modem, caller, already_answered=False)
                caller = ""
                noise_hits = 0
                continue

            if etype == "line":
                u = text.upper()
                # Basura PCM decodificada como "texto" → recuperar
                printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
                if len(text) > 40 and printable / max(1, len(text)) < 0.7:
                    noise_hits += 1
                    if noise_hits >= 3:
                        logging.warning("Puerto con PCM residual — force_recover")
                        modem.force_recover()
                        noise_hits = 0
                    continue
                noise_hits = 0
                if "NMBR" in u and "=" in text:
                    caller = text.split("=", 1)[1].strip()
                    logging.info("Caller-ID: %s", caller)
                elif u not in {"OK"} and not any(
                    u.startswith(p) for p in ("DATE", "TIME", "NAME")
                ):
                    logging.info("Modem: %s", text[:80])
    finally:
        modem.hangup()
        modem.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
