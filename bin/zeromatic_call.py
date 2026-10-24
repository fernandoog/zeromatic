#!/opt/zeromatic/venv/bin/python3
"""
zeromatic <telefono> <mensaje...>

Marca con el módem Conexant CX930xx (V.253), reproduce el texto (TTS) y cuelga.
Para el servicio zeromatic mientras dura la llamada y lo vuelve a arrancar al final.

Uso:
  zeromatic 612345678 Hola, esto es una prueba
  zeromatic --dry-run 612345678 Solo genera el WAV
  ZEROMATIC_TIMEOUT=45 zeromatic 912345678 Mensaje largo
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import serial

DEVICE = os.environ.get("MODEM_DEVICE", "/dev/ttyACM0")
BAUD = int(os.environ.get("MODEM_BAUD", "115200"))
TIMEOUT = float(os.environ.get("ZEROMATIC_TIMEOUT", "60"))
SERVICE = os.environ.get("ZEROMATIC_SERVICE", "zeromatic")
TMP_DIR = Path(os.environ.get("ZEROMATIC_TMP", "/tmp/zeromatic"))
_default_log = Path.home() / ".local" / "log" / "zeromatic_call.log"
LOG = Path(os.environ.get("ZEROMATIC_LOG", str(_default_log)))

DLE = 0x10
ETX = 0x03


def setup_log() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)],
    )


def sanitize_number(raw: str) -> str:
    """Solo dígitos, +, * # y comas de pausa Hayes."""
    n = re.sub(r"[^\d+*#,]", "", raw.strip())
    if not n or not re.search(r"\d", n):
        raise SystemExit(f"Número inválido: {raw!r}")
    if len(re.sub(r"\D", "", n)) > 20:
        raise SystemExit("Número demasiado largo")
    return n


def systemctl(*args: str) -> None:
    """systemctl; prueba sin sudo, luego sudo -n, luego sudo interactivo."""
    attempts = [
        ["systemctl", *args],
        ["sudo", "-n", "systemctl", *args],
        ["sudo", "systemctl", *args],
    ]
    last: Exception | None = None
    for cmd in attempts:
        try:
            subprocess.check_call(cmd)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            last = e
            continue
    raise RuntimeError(f"systemctl {' '.join(args)} falló: {last}")


def stop_contestador() -> None:
    if os.environ.get("ZEROMATIC_SKIP_SERVICE") == "1":
        logging.warning("ZEROMATIC_SKIP_SERVICE=1 — no paro %s", SERVICE)
        return
    logging.info("Parando %s…", SERVICE)
    systemctl("stop", SERVICE)
    time.sleep(1.5)


def start_contestador() -> None:
    if os.environ.get("ZEROMATIC_SKIP_SERVICE") == "1":
        return
    logging.info("Arrancando %s…", SERVICE)
    try:
        systemctl("start", SERVICE)
    except Exception as e:
        logging.error("No pude arrancar %s: %s", SERVICE, e)


def tts_wav(text: str, out: Path) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    raw = TMP_DIR / "tts_raw.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        ["espeak-ng", "-v", "es", "-s", "140", "-a", "200", "-w", str(raw), text],
    )
    subprocess.check_call(
        ["sox", str(raw), "-r", "8000", "-c", "1", "-b", "16", str(out), "vol", "4", "dB"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def wav_to_u8_8k(path: Path) -> bytes:
    tmp = TMP_DIR / "play_u8.wav"
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


def shield_dle(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


class Modem:
    def __init__(self, port: str, baud: int) -> None:
        self.ser = serial.Serial(port, baud, timeout=0.2, rtscts=True)
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def drain(self, seconds: float = 0.5) -> bytes:
        end = time.time() + seconds
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(512)
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.02)
        return bytes(buf)

    def cmd(self, command: str, wait: float = 2.0) -> str:
        self.drain(0.15)
        logging.info("AT >>> %s", command)
        self.write((command + "\r").encode("ascii"))
        end = time.time() + wait
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
                low = bytes(buf).upper()
                if b"OK" in low or b"ERROR" in low or b"VCON" in low or b"CONNECT" in low:
                    if b"BUSY" in low or b"NO CARRIER" in low or b"NO DIALTONE" in low or b"NO ANSWER" in low:
                        break
                    # un poco más por si llega el resto
                    time.sleep(0.15)
                    buf.extend(self.ser.read(self.ser.in_waiting or 0))
                    break
            else:
                time.sleep(0.02)
        text = bytes(buf).decode("ascii", errors="ignore")
        logging.info("AT <<< %r", text.replace("\r", "\\r").replace("\n", "\\n")[:120])
        return text

    def init_voice(self) -> None:
        self.write(b"\r")
        time.sleep(0.2)
        self.drain(0.3)
        for _ in range(2):
            self.write(bytes([DLE, ETX]))
            time.sleep(0.05)
        self.drain(0.3)
        self.cmd("AT", wait=1.5)
        self.cmd("AT+FCLASS=8", wait=2.0)
        self.cmd("ATS0=0", wait=1.5)

    def dial_voice(self, number: str, timeout: float) -> bool:
        # Punto y coma = llamada de voz (V.253 / Hayes voice)
        dial = f"ATDT{number};"
        self.drain(0.2)
        logging.info("Marcando %s …", number)
        self.write((dial + "\r").encode("ascii"))
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
                u = bytes(buf).upper()
                if b"BUSY" in u:
                    logging.error("Ocupado")
                    return False
                if b"NO DIALTONE" in u or b"NO DIAL TONE" in u:
                    logging.error("Sin tono de marcación")
                    return False
                if b"NO ANSWER" in u:
                    logging.error("No contestan")
                    return False
                if b"NO CARRIER" in u:
                    logging.error("No carrier / colgaron")
                    return False
                if b"VCON" in u or b"CONNECT" in u:
                    logging.info("Contestaron (VCON/CONNECT)")
                    time.sleep(0.4)
                    return True
                # Algunos firmware responden solo OK al ; de voz
                if b"OK" in u and b"ERROR" not in u and len(buf) > 2:
                    # Esperar un poco más por si llega VCON
                    extra_end = time.time() + 8
                    while time.time() < extra_end:
                        c2 = self.ser.read(256)
                        if c2:
                            buf.extend(c2)
                            u2 = bytes(buf).upper()
                            if b"VCON" in u2 or b"CONNECT" in u2:
                                logging.info("Contestaron (tardío)")
                                return True
                            if b"BUSY" in u2 or b"NO CARRIER" in u2 or b"NO ANSWER" in u2:
                                return False
                        else:
                            time.sleep(0.05)
                    logging.info("OK de marcación voz — asumo línea abierta")
                    return True
            else:
                time.sleep(0.05)
        logging.error("Timeout esperando contestación (%.0fs)", timeout)
        return False

    def select_line(self) -> None:
        self.cmd("AT+VSM=1,8000,0,0", wait=1.5)
        self.cmd("AT+VSD=0,40", wait=1.5)
        r = self.cmd("AT+VLS=1", wait=1.5)
        if "ERROR" in r.upper() and "OK" not in r.upper():
            self.cmd("AT+VLS=5", wait=1.5)

    def play_pcm_u8(self, pcm: bytes) -> bool:
        self.select_line()
        r = self.cmd("AT+VTX", wait=4.0)
        u = r.upper()
        if "ERROR" in u and "CONNECT" not in u and "OK" not in u:
            logging.warning("AT+VTX rechazado")
            return False
        time.sleep(0.25)
        payload = shield_dle(pcm)
        chunk = 1024
        rate = 8000.0 / 1.02
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
                return False
            time.sleep(0.05)
        return True

    def hangup(self) -> None:
        for _ in range(4):
            try:
                self.write(bytes([DLE, ETX]))
            except Exception:
                pass
            time.sleep(0.08)
        self.drain(0.5)
        self.cmd("AT+VLS=0", wait=1.2)
        self.cmd("ATH", wait=2.0)
        self.cmd("AT+FCLASS=0", wait=1.5)


def main() -> int:
    setup_log()
    p = argparse.ArgumentParser(
        prog="zeromatic",
        description="Marca, dice un texto (TTS) y cuelga (módem Zeromatic / contestador).",
    )
    p.add_argument("--dry-run", action="store_true", help="Solo TTS; no marca ni toca el módem")
    p.add_argument("--timeout", type=float, default=TIMEOUT, help="Segundos esperando contestación")
    p.add_argument("telefono", help="Número a marcar (dígitos; se admite + y , de pausa)")
    p.add_argument("mensaje", nargs="+", help="Texto a sintetizar y reproducir")
    args = p.parse_args()

    number = sanitize_number(args.telefono)
    text = " ".join(args.mensaje).strip()
    if not text:
        raise SystemExit("Mensaje vacío")

    wav = TMP_DIR / "outbound.wav"
    logging.info("TTS: %r → %s", text, wav)
    tts_wav(text, wav)

    if args.dry_run:
        logging.info("Dry-run OK (%s, %d bytes)", wav, wav.stat().st_size)
        print(wav)
        return 0

    stopped = False
    modem: Modem | None = None

    def _cleanup(*_a) -> None:  # noqa: ANN002
        nonlocal modem
        if modem:
            try:
                modem.hangup()
            except Exception:
                pass
            try:
                modem.close()
            except Exception:
                pass
            modem = None
        if stopped:
            start_contestador()

    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(1)))
    signal.signal(signal.SIGINT, lambda *_: (_cleanup(), sys.exit(1)))

    try:
        stop_contestador()
        stopped = True
        time.sleep(1.0)
        modem = Modem(DEVICE, BAUD)
        modem.init_voice()
        if not modem.dial_voice(number, timeout=args.timeout):
            modem.hangup()
            return 2
        # Breve pausa para que contesten / saludo humano
        time.sleep(1.0)
        pcm = wav_to_u8_8k(wav)
        ok = modem.play_pcm_u8(pcm)
        if not ok:
            logging.warning("Reintento de reproducción…")
            modem.play_pcm_u8(pcm)
        time.sleep(0.5)
        modem.hangup()
        logging.info("Llamada terminada OK")
        return 0
    except serial.SerialException as e:
        logging.error("Puerto serie: %s", e)
        return 3
    except Exception:
        logging.exception("Fallo en llamada")
        return 1
    finally:
        if modem:
            try:
                modem.close()
            except Exception:
                pass
        if stopped:
            start_contestador()


if __name__ == "__main__":
    sys.exit(main())
