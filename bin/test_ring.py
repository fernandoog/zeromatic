#!/usr/bin/env python3
"""Comprueba tono y marca ATDT (sin ;) dejando ~50s de timbre en el destino."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

import serial

NUMBER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TEST_PHONE", "")
if not NUMBER:
    raise SystemExit("Uso: test_ring.py <número>   o   TEST_PHONE=6XXXXXXXX test_ring.py")
DEVICE = "/dev/ttyACM0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def cmd(ser, command, wait=3.0):
    ser.reset_input_buffer()
    ser.write((command + "\r").encode())
    ser.flush()
    end = time.time() + wait
    buf = b""
    while time.time() < end:
        chunk = ser.read(512)
        if chunk:
            buf += chunk
            u = buf.upper()
            if any(
                x in u
                for x in (
                    b"OK",
                    b"ERROR",
                    b"CONNECT",
                    b"NO CARRIER",
                    b"BUSY",
                    b"NO DIALTONE",
                    b"NO DIAL TONE",
                    b"NO ANSWER",
                )
            ):
                time.sleep(0.25)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    text = buf.decode("ascii", errors="ignore")
    logging.info(">>> %s | <<< %r", command, text.replace("\r", "\\r").replace("\n", "\\n"))
    return text


def main():
    subprocess.call(["systemctl", "stop", "zeromatic.service"])
    time.sleep(1.2)
    ser = serial.Serial(DEVICE, 115200, timeout=0.2, rtscts=True)
    time.sleep(0.5)
    try:
        cmd(ser, "ATH")
        cmd(ser, "ATZ", 4)
        cmd(ser, "ATE0")
        cmd(ser, "ATQ0V1")
        cmd(ser, "AT+FCLASS=0")

        logging.info("=== 1) ¿Hay tono? ATX4 + ATDTW (W=esperar tono) ===")
        cmd(ser, "ATX4")
        r = cmd(ser, "ATDTW,", wait=12)
        if "NO DIAL" in r.upper():
            logging.error("NO HAY TONO DE MARCADO — cable en puerto LINE de la roseta/módem")
            cmd(ser, "ATH")
            return 2
        cmd(ser, "ATH")

        logging.info("=== 2) Descolgar ATH1 3s (debería oírse tono si hubiera auricular) ===")
        cmd(ser, "ATH1", wait=5)
        time.sleep(3)
        cmd(ser, "ATH")

        logging.info("=== 3) MARCANDO ATDT%s — el móvil debe SONAR ahora (~50s) ===", NUMBER)
        cmd(ser, "ATX4")
        cmd(ser, "ATS7=55")
        cmd(ser, "ATS6=4")  # wait before dial
        t0 = time.time()
        r = cmd(ser, f"ATDT{NUMBER}", wait=60)
        logging.info("Fin marcado (%.1fs): %s", time.time() - t0, (r or "").strip())
        cmd(ser, "ATH", 3)

        if "NO DIAL" in (r or "").upper():
            logging.error("Sin tono al marcar. Revisa: roseta → puerto LINE del módem (no PHONE).")
            return 2
        if "BUSY" in (r or "").upper():
            logging.warning("BUSY — la red respondió (línea OK) pero ocupado.")
            return 0
        if "NO CARRIER" in (r or "").upper():
            logging.info(
                "NO CARRIER tras espera: el módem marcó y esperó. "
                "Si el móvil NO sonó, la toma no es PSTN o el cable va al puerto PHONE."
            )
            return 1
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass
        subprocess.call(["systemctl", "start", "zeromatic.service"])
        logging.info("Contestador rearrancado")


if __name__ == "__main__":
    raise SystemExit(main())
