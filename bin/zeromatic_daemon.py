#!/usr/bin/env python3
"""
Zeromatic — contestador telefónico.

Soporta:
  - GSM SIM800C (USB-UART, p. ej. /dev/ttyUSB0) vía AT+CREC
  - Conexant CX930xx (V.253, /dev/ttyACM0) legado

Modo: MODEM_TYPE=gsm|conexant|auto (por defecto auto).
"""
from __future__ import annotations

import logging
import os
import re
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import serial

def _detect_root() -> Path:
    """Instalación viva: Zeromatic o rutas legadas."""
    for p in (Path("/opt/zeromatic"), Path("/opt/buzonera"), Path("/opt/contestador")):
        if p.is_dir():
            return p
    return Path("/opt/zeromatic")


INSTALL_ROOT = Path(os.environ.get("ZEROMATIC_ROOT", str(_detect_root())))


def _detect_etc() -> Path:
    for p in (Path("/etc/zeromatic"), Path("/etc/buzonera"), Path("/etc/contestador")):
        if p.is_dir() or (p / "email.env").is_file():
            return p
    return Path("/etc") / INSTALL_ROOT.name


ETC_ROOT = _detect_etc()


def _default_device() -> str:
    if Path("/dev/ttyUSB0").exists():
        return "/dev/ttyUSB0"
    return "/dev/ttyACM0"


DEVICE = os.environ.get("MODEM_DEVICE", _default_device())
BAUD = int(os.environ.get("MODEM_BAUD", "115200"))
RINGS_TO_ANSWER = int(os.environ.get("RINGS_TO_ANSWER", "1"))
MAX_RECORD_SEC = int(os.environ.get("MAX_RECORD_SEC", "35"))
# Segundos de silencio / tono de ocupado tras colgar antes de cortar la grabación
SILENCE_HANG_SEC = float(os.environ.get("SILENCE_HANG_SEC", "2.0"))
# |sample-128| medio por ventana para considerar "silencio" (u8 PCM)
SILENCE_RMS_THRESH = float(os.environ.get("SILENCE_RMS_THRESH", "10"))
# No cortar por silencio antes de haber grabado al menos esto
MIN_MESSAGE_SEC = float(os.environ.get("MIN_MESSAGE_SEC", "1.0"))
GREETING_WAV = Path(os.environ.get("GREETING_WAV", str(INSTALL_ROOT / "media" / "greeting.wav")))
INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/var/spool/voice/incoming"))
ON_MESSAGE = Path(os.environ.get("ON_MESSAGE", str(INSTALL_ROOT / "bin" / "on_message.sh")))
_log_default = f"/var/log/{INSTALL_ROOT.name}_daemon.log"
LOG_PATH = Path(
    os.environ.get(
        "ZEROMATIC_LOG",
        os.environ.get("BUZONERA_LOG", os.environ.get("CONTESTADOR_LOG", _log_default)),
    )
)
SMS_FORWARD = os.environ.get("SMS_FORWARD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
SMS_MAIL_TO = os.environ.get("SMS_MAIL_TO", "").strip()
SMS_POLL_SEC = float(os.environ.get("SMS_POLL_SEC", "45"))
SMS_LLAMAR = os.environ.get("SMS_LLAMAR", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
# Segundos tras marcar antes de reproducir el TTS (timbre + descolgar)
SMS_CALL_WAIT = float(os.environ.get("SMS_CALL_WAIT", "22"))


def resolve_modem_type() -> str:
    """gsm | conexant."""
    raw = os.environ.get("MODEM_TYPE", "auto").strip().lower()
    if raw in {"gsm", "sim800", "sim800c"}:
        return "gsm"
    if raw in {"conexant", "pstn", "v253", "cx93010"}:
        return "conexant"
    # auto
    if "ttyUSB" in DEVICE or "ch341" in DEVICE.lower():
        return "gsm"
    if Path("/dev/ttyUSB0").exists() and not Path("/dev/ttyACM0").exists():
        return "gsm"
    return "conexant"


MODEM_TYPE = resolve_modem_type()

DLE = 0x10
ETX = 0x03
DC4 = 0x14
NUL = 0x00

running = True


def setup_log() -> None:
    from logging.handlers import RotatingFileHandler

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Conservar historial para depurar; rotar para no llenar el disco.
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=8, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            file_handler,
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
        """Beep corto (~0.4 s) justo antes de grabar."""
        self.cmd("AT+VLS=1", wait=0.6, expect_ok=False)
        r = self.cmd("AT+VTS=[933,0,400]", wait=1.2, expect_ok=False)
        if "ERROR" in r.upper() and "OK" not in r.upper():
            beep = synth_beep_u8(933, 0.4)
            self.cmd("AT+VLS=1", wait=0.5, expect_ok=False)
            self._vtx_send(beep)

    def _finish_record(self, out_wav: Path, data: bytes, reason: str) -> int:
        """Cierra VRX, recorta silencio final (no el inicio) y guarda WAV."""
        try:
            self.write(bytes([DLE, ETX]))
        except Exception:
            pass
        time.sleep(0.15)
        try:
            self.ser.read(self.ser.in_waiting or 0)
        except Exception:
            pass
        trimmed = trim_u8_pcm(data, trim_lead=False)
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
        # No vaciar el buffer: el primer audio ya está llegando (si lo tiramos,
        # se pierde el inicio del mensaje — ~1–2 s).
        time.sleep(0.05)
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
    tmp = Path("/tmp/zeromatic_play.wav")
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


def trim_u8_pcm(
    pcm: bytes,
    thresh: int = 10,
    pad_ms: int = 200,
    trim_lead: bool = False,
) -> bytes:
    """Recorta silencio del PCM u8 (centro 128).

    Por defecto solo recorta el final: el inicio del mensaje a menudo es voz
    suave y un trim de cabeza se come las primeras palabras.
    """
    if not pcm:
        return pcm
    thr = thresh
    first = 0
    last = len(pcm) - 1
    if trim_lead:
        # Como mucho 0.3 s de silencio inicial
        lead_cap = int(8000 * 0.3)
        while first < len(pcm) and first < lead_cap and abs(pcm[first] - 128) <= thr:
            first += 1
        first = max(0, first - int(8000 * pad_ms / 1000))
    while last > first and abs(pcm[last] - 128) <= thr:
        last -= 1
    pad = int(8000 * pad_ms / 1000)
    last = min(len(pcm) - 1, last + pad)
    # Tono de marcación final (seno estable)
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
    """Número de esta línea (Called-ID), desde env o email.env."""
    for key in ("CALLED_ID", "LINE_NUMBER"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    env_path = ETC_ROOT / "email.env"
    if not env_path.is_file():
        for alt in (Path("/etc/zeromatic/email.env"), Path("/etc/contestador/email.env")):
            if alt.is_file():
                env_path = alt
                break
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


def parse_sms_llamar(text: str) -> tuple[str, str] | None:
    """'LLAMAR 600000000 Esto es un mensaje' → (número, texto TTS)."""
    if not text:
        return None
    m = re.match(
        r"^\s*LLAMAR\s+(\+?\d[\d\s.\-]{6,18}\d)\s+(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    raw_num = re.sub(r"[^\d+]", "", m.group(1))
    spoken = " ".join(m.group(2).split()).strip()
    if not raw_num or not spoken:
        return None
    # ATD del SIM800: dígitos (y + opcional)
    number = re.sub(r"[^\d+]", "", raw_num)
    if number.count("+") > 1 or ( "+" in number and not number.startswith("+")):
        number = number.replace("+", "")
    if len(re.sub(r"\D", "", number)) < 7:
        return None
    return number, spoken


def tts_espeak_wav(text: str, out: Path) -> Path:
    """Sintetiza castellano con espeak-ng → WAV 8 kHz 16-bit mono."""
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = Path("/tmp/zeromatic_sms_tts_raw.wav")
    subprocess.check_call(
        ["espeak-ng", "-v", "es", "-s", "120", "-a", "200", "-w", str(raw), text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        [
            "sox",
            str(raw),
            "-r",
            "8000",
            "-c",
            "1",
            "-b",
            "16",
            "-e",
            "signed-integer",
            str(out),
            "vol",
            "4",
            "dB",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def sms_dial_and_speak(modem, number: str, spoken: str) -> bool:
    """Marca number y reproduce `spoken` (TTS) por CREC en el SIM800."""
    remote = r"C:\User\outbound.wav"
    wav = Path("/tmp/zeromatic_sms_tts.wav")
    logging.info("SMS LLAMAR: marcando %s — TTS %r", number, spoken[:120])
    try:
        tts_espeak_wav(spoken, wav)
    except Exception:
        logging.exception("TTS espeak falló")
        return False
    try:
        # Liberar FAT (saludo) para caber el TTS
        try:
            modem.release_greeting_space()
        except Exception:
            pass
        modem.cmd(f'AT+FSDEL="{remote}"', wait=1.5, expect_ok=False)
        modem.upload_wav(wav, remote)
        if not modem.dial_voice(number, timeout=45.0):
            modem.hangup()
            return False
        logging.info("Esperando hasta %.0fs a que contesten…", SMS_CALL_WAIT)
        t_end = time.time() + SMS_CALL_WAIT
        while time.time() < t_end:
            et, tx = modem.read_event(timeout=0.5)
            u = (tx or "").upper()
            if "NO CARRIER" in u or "BUSY" in u or "NO ANSWER" in u:
                logging.error("Destino no contestó / ocupado: %r", (tx or "")[:80])
                modem.hangup()
                return False
            if et == "line" and "+CLCC:" in u:
                # MO active (dir=0,stat=0) → contestó
                try:
                    body = tx.split(":", 1)[1]
                    parts = [p.strip() for p in body.split(",")]
                    if int(parts[1]) == 0 and int(parts[2]) == 0:
                        logging.info("Destino descolgó (CLCC active)")
                        break
                except Exception:
                    pass
        try:
            modem.beep_crec()
        except Exception:
            modem.beep_dtmf()
        time.sleep(0.25)
        modem.play_remote(remote, volume=100)
        time.sleep(0.35)
        modem.play_remote(remote, volume=100)
        modem.hangup()
        logging.info("SMS LLAMAR completada (%s)", number)
        return True
    except Exception:
        logging.exception("SMS LLAMAR falló")
        try:
            modem.hangup()
        except Exception:
            pass
        return False
    finally:
        try:
            modem.force_recover()
        except Exception:
            logging.exception("Recover tras LLAMAR falló")
        try:
            modem.upload_prompt_files(GREETING_WAV)
        except Exception:
            logging.exception("No se pudo reponer saludo tras LLAMAR")
        try:
            wav.unlink(missing_ok=True)
            Path("/tmp/zeromatic_sms_tts_raw.wav").unlink(missing_ok=True)
        except Exception:
            pass


def forward_sms_and_delete(modem, index: int | None = None) -> int:
    """Lee SMS, reenvía por correo, ejecuta LLAMAR si aplica, y borra si el mail OK.

    Devuelve cuántos SMS se reenviaron y borraron.
    """
    if not SMS_FORWARD and not SMS_LLAMAR:
        return 0
    try:
        from send_mail import send_sms_forward  # noqa: WPS433
    except Exception:
        sys.path.insert(0, str(INSTALL_ROOT / "bin"))
        from send_mail import send_sms_forward  # noqa: WPS433

    done = 0
    indices: list[int]
    if index is not None:
        indices = [index]
    else:
        try:
            indices = modem.sms_list_indices()
        except Exception:
            logging.exception("No se pudo listar SMS")
            return 0
    for idx in indices:
        try:
            msg = modem.sms_read(idx)
            if not msg:
                logging.warning("SMS %d ilegible — skip", idx)
                continue
            sender = msg.get("sender") or "?"
            when = msg.get("time") or ""
            text = msg.get("text") or ""
            logging.info(
                "SMS#%d de %s (%s): %s",
                idx,
                sender,
                when,
                (text[:80] + "…") if len(text) > 80 else text,
            )
            mail_ok = False
            if SMS_FORWARD:
                send_sms_forward(sender, when, text, mail_to=SMS_MAIL_TO or None)
                mail_ok = True
            else:
                mail_ok = True  # sin reenvío: permitir borrar tras LLAMAR

            llamar = parse_sms_llamar(text) if SMS_LLAMAR else None
            if llamar:
                number, spoken = llamar
                logging.info("SMS#%d comando LLAMAR → %s", idx, number)
                ok_call = sms_dial_and_speak(modem, number, spoken)
                if not ok_call:
                    logging.error("SMS#%d LLAMAR no completó la llamada", idx)

            # Borrar si el correo OK (o reenvío desactivado). LLAMAR fallido no retiene el SMS
            # para no reintentar en bucle; el correo ya documenta el comando.
            if mail_ok and modem.sms_delete(idx):
                done += 1
                logging.info("SMS#%d procesado y borrado", idx)
            elif mail_ok:
                logging.error("SMS#%d procesado pero no se borró de la SIM", idx)
        except Exception:
            logging.exception("Fallo procesando SMS#%s — se conserva en la SIM", idx)
    return done


def seize_and_play(modem: Modem, caller: str, already_answered: bool = False) -> None:
    """Descolgar al instante, locución y grabar (Conexant V.253)."""
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
        # Preparar codec ANTES del beep y grabar al momento (si no, el usuario
        # habla ~1–2 s mientras aún mandamos AT y se corta el inicio).
        modem.cmd("AT+VLS=1", wait=0.5, expect_ok=False)
        modem.cmd("AT+VSM=1,8000,0,0", expect_ok=False)
        modem.beep()
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


def seize_and_play_gsm(modem, caller: str) -> None:
    """Contestador SIM800: ATA → CREC play greeting → CREC record → download."""
    from modem_gsm import GREETING_REMOTE, RECORD_ID  # noqa: WPS433

    logging.info("Atendiendo GSM (caller=%s)", caller or "?")
    try:
        try:
            modem.ser.reset_input_buffer()
        except Exception:
            pass
        modem.answer_now()
        time.sleep(0.5)
        # Preparar slot / liberar FAT durante la locución (no tras el pitido).
        try:
            modem.prepare_record_slot(RECORD_ID)
        except Exception:
            logging.exception("No se pudo preparar slot de grabación")
        try:
            modem.play_remote(GREETING_REMOTE, volume=95)
        except Exception:
            logging.exception("Play greeting falló")
        try:
            modem.release_greeting_space()
        except Exception:
            logging.exception("No se pudo liberar espacio del saludo")
        # Pitido WAV por CREC (audible en red). Luego CREC=1 al instante.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        wav = INCOMING_DIR / f"msg_{stamp}.wav"
        try:
            modem.beep_crec()
        except Exception:
            logging.exception("Pitido falló")
        if not modem.start_record(RECORD_ID, max_sec=MAX_RECORD_SEC):
            raise RuntimeError("CREC no arrancó")
        size = modem.wait_record_done(RECORD_ID, max_sec=MAX_RECORD_SEC)
        modem.hangup()
        n = 0
        if size > 50:
            n = modem.download_record(RECORD_ID, wav, total=size)
        modem.force_recover()
        # Reponer saludo + pitido para la siguiente llamada
        try:
            modem.upload_prompt_files(GREETING_WAV)
        except Exception:
            logging.exception("No se pudo reponer saludo/pitido")
        if n > 1000:
            logging.info("Mensaje GSM guardado %s (%d bytes)", wav, n)
            process_message(wav, caller)
        else:
            logging.warning("Grabación GSM corta/vacía (%d) — sin pipeline", n)
            try:
                wav.unlink(missing_ok=True)
            except Exception:
                pass
        logging.info("Listo para siguiente llamada (GSM)")
    except Exception:
        logging.exception("Error en llamada GSM")
        try:
            modem.hangup()
        except Exception:
            pass
        try:
            modem.force_recover()
        except Exception:
            logging.exception("Recover GSM falló")


def main_gsm() -> int:
    from modem_gsm import (  # noqa: WPS433
        GREETING_REMOTE,
        GsmModem,
        parse_caller_from_urc,
        parse_clip,
    )

    ensure_greeting()
    logging.info("Instalación: %s (MODEM_TYPE=gsm device=%s)", INSTALL_ROOT, DEVICE)
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    modem = GsmModem(DEVICE, BAUD)
    try:
        modem.init()
        # Saludo + pitido en AMR (la FAT del SIM800C apenas tiene ~180 KiB)
        try:
            modem.upload_prompt_files(GREETING_WAV)
        except Exception:
            logging.exception(
                "No se pudo subir saludo/pitido a SIM800 (%s) — usaré DTMF",
                GREETING_REMOTE,
            )
        caller = ""
        rings = 0
        last_clcc_poll = 0.0
        last_sms_poll = 0.0
        need = max(1, RINGS_TO_ANSWER)
        logging.info(
            "Esperando llamadas/SMS GSM (RING %d; SMS→%s)…",
            need,
            SMS_MAIL_TO or "(MAIL_TO)",
        )
        # Vaciar bandeja pendiente al arrancar
        if SMS_FORWARD:
            try:
                n = forward_sms_and_delete(modem)
                if n:
                    logging.info("SMS pendientes reenviados al arranque: %d", n)
            except Exception:
                logging.exception("Barrido SMS inicial falló")
            last_sms_poll = time.time()
        while running:
            etype, text = modem.read_event(timeout=0.8)
            if not etype:
                now = time.time()
                # Respaldo: si el URC RING se pierde, CLCC detecta llamada entrante.
                if now - last_clcc_poll >= 2.0:
                    last_clcc_poll = now
                    etype, text = modem.poll_incoming()
                # Barrido periódico de SMS por si se perdió +CMTI
                if not etype and SMS_FORWARD and now - last_sms_poll >= SMS_POLL_SEC:
                    last_sms_poll = now
                    try:
                        forward_sms_and_delete(modem)
                    except Exception:
                        logging.exception("Barrido SMS periódico falló")
                if not etype:
                    continue
            # El número suele venir en el mismo bloque CLCC/CLIP que el RING
            extracted = parse_caller_from_urc(text)
            if extracted:
                caller = extracted
            if etype == "sms":
                idx = modem.parse_cmti_index(text)
                logging.info("SMS nuevo indicado: %s", text.strip())
                try:
                    forward_sms_and_delete(modem, index=idx)
                except Exception:
                    logging.exception("Procesar +CMTI falló")
                last_sms_poll = time.time()
                continue
            if etype == "ring":
                rings += 1
                logging.info(
                    "RING %d/%d (%s) caller=%s",
                    rings,
                    need,
                    text.replace("\r", "\\r").replace("\n", "\\n")[:120],
                    caller or "?",
                )
                if rings >= need:
                    if not caller:
                        caller = modem.fetch_caller_id()
                        logging.info("Caller-ID (fetch): %s", caller or "?")
                    seize_and_play_gsm(modem, caller)
                    caller = ""
                    rings = 0
                    last_clcc_poll = time.time()
                    # Tras la llamada, vaciar SMS llegados mientras hablábamos
                    if SMS_FORWARD:
                        try:
                            forward_sms_and_delete(modem)
                        except Exception:
                            logging.exception("Barrido SMS post-llamada falló")
                        last_sms_poll = time.time()
                continue
            if etype == "line":
                u = text.upper()
                if u.startswith("+CLIP:") or u.startswith("+CLCC:"):
                    if caller:
                        logging.info("Caller-ID: %s", caller)
                elif u.startswith("+CMTI:"):
                    idx = modem.parse_cmti_index(text)
                    try:
                        forward_sms_and_delete(modem, index=idx)
                    except Exception:
                        logging.exception("Procesar +CMTI (line) falló")
                    last_sms_poll = time.time()
                elif "NO CARRIER" in u:
                    logging.info("NO CARRIER (idle)")
                    rings = 0
                elif u not in {"OK"}:
                    logging.info("Modem: %s", text[:100])
    finally:
        try:
            modem.hangup()
        except Exception:
            pass
        modem.close()
    return 0


def main_conexant() -> int:
    ensure_greeting()
    logging.info("Instalación: %s (MODEM_TYPE=conexant device=%s)", INSTALL_ROOT, DEVICE)
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    modem = Modem(DEVICE, BAUD)
    try:
        modem.init()
        caller = ""
        rings = 0
        logging.info(
            "Esperando llamadas (contesta al RING %d; Caller-ID entre timbres)…",
            RINGS_TO_ANSWER,
        )
        noise_hits = 0
        while running:
            etype, text = modem.read_event(timeout=1.0)
            if not etype:
                noise_hits = 0
                continue

            if etype == "vcon" or (
                etype == "line" and ("VCON" in text.upper() or text.upper().startswith("CONNECT"))
            ):
                logging.info("VCON → locución (caller=%s)", caller or "?")
                seize_and_play(modem, caller, already_answered=True)
                caller = ""
                rings = 0
                noise_hits = 0
                continue

            if etype == "ring" or (etype == "line" and "RING" in text.upper()):
                rings += 1
                logging.info(
                    "RING %d/%d (%s) caller=%s",
                    rings,
                    RINGS_TO_ANSWER,
                    text or etype,
                    caller or "?",
                )
                # En España el CLIP suele llegar entre el 1.º y el 2.º timbre.
                if rings >= max(1, RINGS_TO_ANSWER):
                    seize_and_play(modem, caller, already_answered=False)
                    caller = ""
                    rings = 0
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
                        rings = 0
                    continue
                noise_hits = 0
                if "NMBR" in u and "=" in text:
                    caller = text.split("=", 1)[1].strip()
                    logging.info("Caller-ID: %s", caller)
                elif u.startswith("DATE=") or u.startswith("TIME=") or u.startswith("NAME="):
                    logging.info("CID: %s", text.strip()[:80])
                elif u not in {"OK"}:
                    logging.info("Modem: %s", text[:80])
    finally:
        modem.hangup()
        modem.close()
    return 0


def main() -> int:
    setup_log()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    # Regenerar locución con el texto actual (si el script existe)
    greet_sh = INSTALL_ROOT / "bin" / "make_greeting_wav.sh"
    if greet_sh.is_file():
        subprocess.call([str(greet_sh)])
    logging.info("MODEM_TYPE efectivo: %s", MODEM_TYPE)
    if MODEM_TYPE == "gsm":
        # Asegurar import desde /opt/zeromatic/bin
        bin_dir = str(INSTALL_ROOT / "bin")
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        return main_gsm()
    return main_conexant()


if __name__ == "__main__":
    raise SystemExit(main())
