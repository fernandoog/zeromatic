#!/usr/bin/env python3
"""Módem GSM SIM800 (SIMCom) vía USB-UART (p. ej. CH340 → /dev/ttyUSB0).

Audio de contestador: no hay V.253. Locución y grabación van por AT+CREC
(WAV 8 kHz 16-bit en la FAT interna C:\\User\\...).
"""
from __future__ import annotations

import logging
import os
import struct
import subprocess
import time
import wave
from pathlib import Path

import serial

GREETING_REMOTE = os.environ.get("GSM_GREETING_PATH", r"C:\User\greeting.wav")
RECORD_ID = int(os.environ.get("GSM_RECORD_ID", "2"))
SIM_PIN = os.environ.get("SIM_PIN", "").strip()
DISABLE_SIM_PIN = os.environ.get("DISABLE_SIM_PIN", "1").strip() not in {
    "0",
    "false",
    "no",
    "off",
}


class GsmModem:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        # CH340 / SIM800: sin RTS/CTS hardware
        self.ser = serial.Serial(port, baud, timeout=0.25, rtscts=False, dsrdtr=False)
        time.sleep(0.35)
        self.ser.reset_input_buffer()
        self._line_buf = bytearray()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def _drain(self, seconds: float = 0.4) -> None:
        t0 = time.time()
        while time.time() - t0 < seconds:
            n = self.ser.in_waiting
            if n:
                self.ser.read(n)
            else:
                time.sleep(0.03)

    def cmd(self, command: str, wait: float = 2.0, expect_ok: bool = True) -> str:
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
                u = buf.upper()
                if b"OK" in u or b"ERROR" in u:
                    time.sleep(0.08)
                    if self.ser.in_waiting:
                        buf += self.ser.read(self.ser.in_waiting)
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("latin1", errors="replace")
        show = text.replace("\r", "\\r").replace("\n", "\\n")
        logging.info("AT >>> %s <<< %r", command, show[:140])
        if expect_ok and "ERROR" in text.upper() and "OK" not in text.upper():
            logging.warning("Modem ERROR en %s", command)
        return text

    def probe_at(self, wait: float = 1.2) -> bool:
        r = self.cmd("AT", wait=wait, expect_ok=False)
        return "OK" in r.upper()

    def _wait_cpin(self, timeout: float = 60.0) -> str:
        """Espera estado SIM. Devuelve READY / SIM PIN / SIM PUK / NOT INSERTED / …"""
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            r = self.cmd("AT+CPIN?", wait=2.0, expect_ok=False)
            last = r
            u = r.upper()
            if "READY" in u:
                return "READY"
            if "SIM PIN" in u:
                return "SIM PIN"
            if "SIM PUK" in u:
                return "SIM PUK"
            if "NOT INSERTED" in u or "SIM NOT INSERTED" in u:
                time.sleep(1.0)
                continue
            time.sleep(1.0)
        logging.error("Timeout CPIN: %r", last[:80])
        return "TIMEOUT"

    def unlock_and_optional_disable_pin(self, pin: str) -> None:
        if not pin:
            return
        st = self._wait_cpin(45.0)
        if st == "SIM PIN":
            logging.info("Desbloqueando SIM con PIN…")
            r = self.cmd(f'AT+CPIN="{pin}"', wait=5.0, expect_ok=False)
            if "OK" not in r.upper():
                raise RuntimeError(f"PIN rechazado: {r[:80]}")
            time.sleep(1.5)
            st = self._wait_cpin(30.0)
        if st != "READY":
            raise RuntimeError(f"SIM no READY tras PIN ({st})")
        if DISABLE_SIM_PIN:
            logging.info("Desactivando petición de PIN (CLCK SC)…")
            r = self.cmd(f'AT+CLCK="SC",0,"{pin}"', wait=6.0, expect_ok=False)
            if "OK" not in r.upper():
                logging.warning("No se pudo desactivar PIN: %r", r[:80])
            else:
                chk = self.cmd('AT+CLCK="SC",2', wait=3.0, expect_ok=False)
                logging.info("Estado lock SC: %r", chk.replace("\r", "\\r").replace("\n", "\\n")[:80])

    def wait_network(self, timeout: float = 90.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            r = self.cmd("AT+CREG?", wait=1.5, expect_ok=False)
            # 0,1 home ; 0,5 roaming
            if ",1" in r or ",5" in r:
                cops = self.cmd("AT+COPS?", wait=3.0, expect_ok=False)
                logging.info("Red OK: %s", cops.strip()[:80])
                return True
            time.sleep(2.0)
        logging.error("Sin registro de red (CREG) en %.0fs", timeout)
        return False

    def init(self) -> None:
        if not self.probe_at():
            self._drain(0.5)
            if not self.probe_at(wait=2.0):
                raise RuntimeError(f"Sin respuesta AT en {self.port}")
        self.cmd("ATE0", expect_ok=False)
        self.cmd("AT+CMEE=2", expect_ok=False)
        self.cmd("AT+CFUN=1", expect_ok=False)
        pin = SIM_PIN
        logging.info("Esperando SIM (hasta ~2 min)…")
        try:
            st = self._wait_cpin(90.0)
        except Exception:
            st = "TIMEOUT"
        if st == "SIM PIN":
            if not pin:
                raise RuntimeError("SIM pide PIN y SIM_PIN no está configurado")
            self.unlock_and_optional_disable_pin(pin)
        elif st == "READY":
            if pin and DISABLE_SIM_PIN:
                # Por si aún tiene PIN activo pero no lo pide en este boot
                self.unlock_and_optional_disable_pin(pin)
        elif st == "SIM PUK":
            raise RuntimeError("SIM bloqueada con PUK")
        else:
            raise RuntimeError(
                "SIM no detectada (AT+CPIN → not inserted / timeout). "
                "Asienta la nano/micro-SIM en el SIM800C (contactos hacia abajo "
                "según la marca de la bandeja) y reinicia el servicio."
            )

        self.wait_network(90.0)
        self.cmd("ATS0=0", expect_ok=False)  # no autoanswer
        self.cmd("AT+CLIP=1", expect_ok=False)
        self.cmd("AT+CRC=1", expect_ok=False)  # +CRING: VOICE
        self.cmd("AT+COLP=0", expect_ok=False)
        self.cmd("AT+CVHU=0", expect_ok=False)
        self.cmd("AT+CLVL=90", expect_ok=False)
        self.cmd("AT+CRSL=60", expect_ok=False)
        self.cmd("AT+CHFA=0", expect_ok=False)
        self.cmd("AT+DDET=0", expect_ok=False)  # DDET=1 bloquea CREC en llamada
        self._drain(0.3)
        logging.info("SIM800 listo en %s", self.port)

    def force_recover(self) -> None:
        logging.info("Recuperando SIM800…")
        self.cmd("ATH", wait=2.0, expect_ok=False)
        self.cmd("AT+CREC=5", wait=1.5, expect_ok=False)
        self.cmd("AT+CREC=2", wait=1.5, expect_ok=False)
        self._drain(0.4)
        if not self.probe_at():
            try:
                self.ser.close()
            except Exception:
                pass
            time.sleep(0.8)
            self.ser = serial.Serial(
                self.port, self.baud, timeout=0.25, rtscts=False, dsrdtr=False
            )
            time.sleep(0.4)
        self.cmd("ATE0", expect_ok=False)
        self.cmd("AT+CLIP=1", expect_ok=False)
        self.cmd("AT+CRC=1", expect_ok=False)
        self.cmd("ATS0=0", expect_ok=False)

    def hangup(self) -> None:
        self.cmd("ATH", wait=2.0, expect_ok=False)
        self.cmd("AT+CHUP", wait=2.0, expect_ok=False)
        self._drain(0.3)

    def answer_now(self) -> None:
        logging.info("Descolgando (ATA)")
        r = self.cmd("ATA", wait=5.0, expect_ok=False)
        if "OK" not in r.upper() and "ERROR" in r.upper():
            logging.warning("ATA falló (%r) — reintento", r[:60])
            time.sleep(0.4)
            self.cmd("ATA", wait=5.0, expect_ok=False)
        time.sleep(0.4)

    def read_event(self, timeout: float = 1.0) -> tuple[str, str]:
        """Devuelve (tipo, texto). tipo: ring | line | ''."""
        end = time.time() + timeout
        while time.time() < end:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._line_buf.extend(chunk)
            elif not self._line_buf:
                time.sleep(0.03)
                continue
            while True:
                raw = bytes(self._line_buf)
                sep = -1
                for i, b in enumerate(raw):
                    if b in (10, 13):
                        sep = i
                        break
                if sep < 0:
                    break
                line = raw[:sep].decode("latin1", errors="replace").strip()
                # consume line + following CR/LF
                j = sep
                while j < len(raw) and raw[j] in (10, 13):
                    j += 1
                del self._line_buf[:j]
                if not line:
                    continue
                u = line.upper()
                if u == "RING" or u.startswith("+CRING"):
                    return "ring", line
                if u.startswith("+CLIP:"):
                    return "line", line
                return "line", line
        return "", ""

    def _fswrite_chunk(self, remote: str, mode: int, payload: bytes, timeout_s: int = 30) -> None:
        """Una escritura FSWRITE (máx. 10240 bytes por comando SIM800)."""
        if not payload:
            return
        if len(payload) > 10240:
            raise ValueError(f"FSWRITE chunk demasiado grande: {len(payload)}")
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        cmd = f'AT+FSWRITE="{remote}",{mode},{len(payload)},{timeout_s}'
        logging.info("AT >>> %s", cmd)
        self.write((cmd + "\r").encode("ascii"))
        end = time.time() + 8
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
                if b"ERROR" in buf.upper():
                    raise RuntimeError(f"FSWRITE rechazado: {buf[:80]!r}")
            else:
                time.sleep(0.03)
        if b">" not in buf:
            raise RuntimeError(f"Sin prompt FSWRITE: {buf[:80]!r}")
        self.write(payload)
        end = time.time() + float(timeout_s) + 5
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(512)
            if chunk:
                buf += chunk
                if b"OK" in buf or b"ERROR" in buf.upper():
                    break
            else:
                time.sleep(0.05)
        if b"OK" not in buf:
            raise RuntimeError(f"FSWRITE incompleto: {buf[:80]!r}")

    def upload_wav(self, local_wav: Path, remote: str = GREETING_REMOTE) -> None:
        """Sube WAV 8 kHz 16-bit mono a la FAT del SIM800 (chunks ≤10240)."""
        data = ensure_gsm_wav_bytes(local_wav)
        # Borrar si existe
        self.cmd(f'AT+FSDEL="{remote}"', wait=2.0, expect_ok=False)
        r = self.cmd(f'AT+FSCREATE="{remote}"', wait=3.0, expect_ok=False)
        if "OK" not in r.upper():
            raise RuntimeError(f"FSCREATE falló: {r[:80]}")
        # mode 0 = inicio; mode 1 = append. Tope SIM800: 10240 B/escritura.
        chunk_size = 10240
        offset = 0
        first = True
        while offset < len(data):
            piece = data[offset : offset + chunk_size]
            self._fswrite_chunk(remote, 0 if first else 1, piece)
            first = False
            offset += len(piece)
        sz = self.cmd(f'AT+FSFLSIZE="{remote}"', wait=2.0, expect_ok=False)
        logging.info("Greeting subido (%d bytes) %s → %s", len(data), local_wav, sz.strip()[:60])

    def play_remote(self, remote: str = GREETING_REMOTE, volume: int = 90) -> None:
        """Reproduce fichero en llamada (canal 0 = red)."""
        r = self.cmd(f'AT+CREC=4,"{remote}",0,{volume}', wait=3.0, expect_ok=False)
        if "OK" not in r.upper():
            logging.warning("CREC play falló (%r) — beep DTMF", r[:60])
            self.beep_dtmf()
            return
        # Esperar fin (+CREC: 0) o timeout por duración estimada
        end = time.time() + 45
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                chunk = self.ser.read(n).decode("latin1", errors="replace")
                if "+CREC: 0" in chunk or "ERROR" in chunk.upper():
                    break
            else:
                time.sleep(0.1)
        self.cmd("AT+CREC=5", wait=1.5, expect_ok=False)

    def beep_dtmf(self) -> None:
        """Beep audible en llamada (DTMF)."""
        self.cmd("AT+VTS=1", wait=2.0, expect_ok=False)
        time.sleep(0.15)
        self.cmd("AT+VTS=1", wait=2.0, expect_ok=False)

    def record_to_id(self, rec_id: int = RECORD_ID, max_sec: float = 59.0) -> int:
        """Graba WAV (format=1) en slot numérico durante la llamada. Devuelve bytes."""
        # Borrar slot previo
        self.cmd(f"AT+CREC=3,{rec_id}", wait=2.0, expect_ok=False)
        limit = max(1, int(max_sec))
        r = self.cmd(f"AT+CREC=1,{rec_id},1,{limit}", wait=3.0, expect_ok=False)
        if "OK" not in r.upper():
            logging.error("No se pudo iniciar CREC: %r", r[:80])
            return 0
        end = time.time() + limit + 5
        size = 0
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                chunk = self.ser.read(n).decode("latin1", errors="replace")
                # +CREC: 2,<id>,<fmt>,<sec>,<bytes>
                if "+CREC: 2," in chunk:
                    for part in chunk.splitlines():
                        if part.startswith("+CREC: 2,"):
                            bits = part.split(",")
                            if len(bits) >= 5:
                                try:
                                    size = int(bits[4].strip())
                                except ValueError:
                                    size = 0
                    break
                if "NO CARRIER" in chunk.upper():
                    self.cmd("AT+CREC=2", wait=2.0, expect_ok=False)
                    time.sleep(0.3)
            else:
                time.sleep(0.1)
        else:
            self.cmd("AT+CREC=2", wait=2.0, expect_ok=False)
            time.sleep(0.4)
            info = self.cmd(f"AT+CREC=7,{rec_id}", wait=2.0, expect_ok=False)
            # +CREC: 7,<id>,<bytes>,<fmt>
            for part in info.splitlines():
                if part.startswith("+CREC: 7,"):
                    bits = part.split(",")
                    if len(bits) >= 3:
                        try:
                            size = int(bits[2].strip())
                        except ValueError:
                            pass
        logging.info("Grabación CREC id=%d → %d bytes", rec_id, size)
        return size

    def download_record(self, rec_id: int, out_wav: Path, total: int | None = None) -> int:
        """Descarga slot CREC (hex) a fichero WAV local."""
        if total is None or total <= 0:
            info = self.cmd(f"AT+CREC=7,{rec_id}", wait=2.0, expect_ok=False)
            total = 0
            for part in info.splitlines():
                if part.startswith("+CREC: 7,"):
                    bits = part.split(",")
                    if len(bits) >= 3:
                        try:
                            total = int(bits[2].strip())
                        except ValueError:
                            total = 0
        if total <= 0:
            out_wav.write_bytes(b"")
            return 0
        raw = bytearray()
        offset = 0
        chunk = 2048  # hex pairs → 1024 bytes; manual max 32K
        while offset < total:
            n = min(chunk, total - offset)
            r = self.cmd(f"AT+CREC=6,{rec_id},{n},{offset}", wait=8.0, expect_ok=False)
            # +CREC: 6,<id>,<len>\r\n<HEX>\r\n\r\nOK
            hexdata = ""
            lines = r.replace("\r\n", "\n").split("\n")
            for i, line in enumerate(lines):
                if line.startswith("+CREC: 6,"):
                    # siguiente línea no vacía = hex
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip():
                            hexdata = lines[j].strip()
                            break
                    break
            if not hexdata:
                logging.warning("CREC=6 sin datos en offset %d", offset)
                break
            try:
                piece = bytes.fromhex(hexdata)
            except ValueError:
                logging.warning("HEX inválido en offset %d", offset)
                break
            raw.extend(piece)
            offset += len(piece)
            if len(piece) == 0:
                break
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(bytes(raw))
        logging.info("Descargado %s (%d bytes)", out_wav, len(raw))
        return len(raw)

    def dial_voice(self, number: str, timeout: float = 45.0) -> bool:
        dial = f"ATD{number};"
        logging.info("Marcando %s …", number)
        self._drain(0.2)
        self.write((dial + "\r").encode("ascii"))
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                u = buf.upper()
                if b"NO CARRIER" in u or b"BUSY" in u or b"NO ANSWER" in u:
                    logging.error("Marcación fallida: %r", buf[:100])
                    return False
                if b"OK" in u and b"ERROR" not in u:
                    logging.info("Marcación OK")
                    return True
            else:
                time.sleep(0.05)
        logging.error("Timeout marcación")
        return False


def ensure_gsm_wav_bytes(path: Path) -> bytes:
    """WAV PCM 16-bit mono 8 kHz (formato que SIM800C reproduce en llamada)."""
    tmp = Path("/tmp/zeromatic_gsm_play.wav")
    subprocess.check_call(
        [
            "sox",
            str(path),
            "-r",
            "8000",
            "-c",
            "1",
            "-b",
            "16",
            "-e",
            "signed-integer",
            str(tmp),
            "vol",
            "3",
            "dB",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return tmp.read_bytes()


def parse_clip(line: str) -> str:
    """+CLIP: \"+34612…\",129 → número."""
    if "+" not in line and '"' not in line:
        return ""
    try:
        # +CLIP: "<num>",…
        q1 = line.find('"')
        q2 = line.find('"', q1 + 1)
        if q1 >= 0 and q2 > q1:
            return line[q1 + 1 : q2].strip()
    except Exception:
        pass
    return ""


def synth_beep_wav_bytes(freq: float = 933.0, seconds: float = 0.35, rate: int = 8000) -> bytes:
    import math

    n = int(rate * seconds)
    pcm = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * freq * i / rate))
        pcm += struct.pack("<h", max(-32767, min(32767, v)))
    data_size = len(pcm)
    hdr = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", data_size)
    return hdr + bytes(pcm)
