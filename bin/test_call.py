#!/usr/bin/env python3
"""Prueba de llamada saliente: marca, conecta voz, reproduce locución y cuelga."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import serial

NUMBER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TEST_PHONE", "")
if not NUMBER:
    raise SystemExit("Uso: test_call.py <número>   o   TEST_PHONE=6XXXXXXXX test_call.py")
DEVICE = "/dev/ttyACM0"
BAUD = 115200
GREETING = Path("/opt/contestador/media/greeting.wav")
DLE, ETX = 0x10, 0x03
ANSWER_WAIT = 25  # segundos para que cojas el móvil

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def cmd(ser: serial.Serial, command: str, wait: float = 2.0) -> str:
    ser.reset_input_buffer()
    ser.write((command + "\r").encode())
    ser.flush()
    end = time.time() + wait
    buf = b""
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            low = buf.upper()
            if any(
                x in low
                for x in (
                    b"OK",
                    b"ERROR",
                    b"CONNECT",
                    b"VCON",
                    b"NO CARRIER",
                    b"BUSY",
                    b"NO DIALTONE",
                    b"NO ANSWER",
                )
            ):
                time.sleep(0.1)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    text = buf.decode("ascii", errors="ignore")
    logging.info(">>> %s | <<< %r", command, text.replace("\r", "\\r").replace("\n", "\\n"))
    return text


def shield_dle(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


def wav_u8(path: Path) -> bytes:
    tmp = Path("/tmp/call_test_play.wav")
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with wave.open(str(tmp), "rb") as w:
        return w.readframes(w.getnframes())


def play(ser: serial.Serial, pcm: bytes) -> bool:
    r = cmd(ser, "AT+VTX", 4)
    if "ERROR" in r.upper() and "OK" not in r.upper() and "CONNECT" not in r.upper():
        return False
    time.sleep(0.25)
    payload = shield_dle(pcm)
    for i in range(0, len(payload), 512):
        ser.write(payload[i : i + 512])
        ser.flush()
        # ritmo aprox. tiempo real 8kHz u8 = 8000 bytes/s
        time.sleep(len(payload[i : i + 512]) / 8000.0 * 0.85)
    ser.write(bytes([DLE, ETX]))
    ser.flush()
    end = time.time() + 12
    while time.time() < end:
        t = ser.read(256)
        if b"OK" in t or b"ERROR" in t:
            logging.info("Fin VTX: %r", t)
            break
        time.sleep(0.05)
    return True


def main() -> int:
    logging.info("Parando contestador…")
    subprocess.call(["systemctl", "stop", "contestador.service"])
    time.sleep(1.5)

    ser = serial.Serial(DEVICE, BAUD, timeout=0.2, rtscts=True)
    time.sleep(0.4)
    ok_play = False
    try:
        cmd(ser, "ATH", 2)
        cmd(ser, "ATZ", 3)
        cmd(ser, "ATE0")
        cmd(ser, "ATQ0V1")
        cmd(ser, "ATX4")
        cmd(ser, "AT+FCLASS=8")
        # Seleccionar línea telefónica antes de marcar
        cmd(ser, "AT+VLS=1", 2)

        logging.info("Marcando %s — ¡contesta el móvil!", NUMBER)
        r = cmd(ser, f"ATD{NUMBER};", wait=60)
        upper = r.upper()
        if any(x in upper for x in ("BUSY", "NO DIALTONE", "NO CARRIER", "NO ANSWER")):
            logging.error("No conectó: %s", r)
            return 1

        logging.info("Marcado OK. Esperando %ds a que cojas…", ANSWER_WAIT)
        time.sleep(ANSWER_WAIT)

        # Asegurar path de voz hacia la línea
        for vls in ("1", "5", "13", "4"):
            cmd(ser, f"AT+VLS={vls}", 2)

        cmd(ser, "AT+VSM=1", 2)
        pcm = wav_u8(GREETING) if GREETING.is_file() else b""
        if not pcm:
            logging.error("Sin greeting.wav")
            return 1

        # Intentar VTX; si falla, probar VTS tono + reintento
        if play(ser, pcm):
            ok_play = True
            logging.info("Locución reproducida")
        else:
            logging.warning("VTX falló con VLS actual, reintentando secuencias…")
            for vls in ("1", "0", "5", "13"):
                cmd(ser, f"AT+VLS={vls}", 2)
                if play(ser, pcm):
                    ok_play = True
                    logging.info("Locución OK con VLS=%s", vls)
                    break

        if not ok_play:
            # Al menos un beep audible por VTS
            cmd(ser, "AT+VTS=[933,0,1500]", 5)
            logging.error("No se pudo enviar audio PCM (VTX ERROR)")

        time.sleep(1)
        logging.info("Colgando…")
        try:
            ser.write(bytes([DLE, ETX]))
        except Exception:
            pass
        time.sleep(0.2)
        cmd(ser, "ATH", 3)
        cmd(ser, "AT+FCLASS=0", 2)
        return 0 if ok_play else 2
    finally:
        try:
            ser.close()
        except Exception:
            pass
        subprocess.call(["systemctl", "start", "contestador.service"])
        logging.info("Contestador rearrancado")


if __name__ == "__main__":
    raise SystemExit(main())
