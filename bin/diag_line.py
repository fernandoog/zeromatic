#!/usr/bin/env python3
"""Diagnóstico de línea telefónica del módem Conexant."""
from __future__ import annotations

import logging
import subprocess
import sys
import time

import serial

DEVICE = "/dev/ttyACM0"
BAUD = 115200
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def cmd(ser: serial.Serial, command: str, wait: float = 3.0) -> str:
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
                    b"VCON",
                    b"RING",
                )
            ):
                time.sleep(0.15)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    text = buf.decode("ascii", errors="ignore")
    logging.info(">>> %-20s <<< %r", command, text.replace("\r", "\\r").replace("\n", "\\n"))
    return text


def main() -> int:
    subprocess.call(["systemctl", "stop", "zeromatic.service"])
    time.sleep(1.5)
    ser = serial.Serial(DEVICE, BAUD, timeout=0.2, rtscts=True)
    time.sleep(0.5)
    try:
        cmd(ser, "ATH")
        cmd(ser, "ATZ", 4)
        cmd(ser, "ATE0")
        cmd(ser, "ATQ0V1")
        cmd(ser, "ATI")
        cmd(ser, "ATI3")
        cmd(ser, "AT+GMI", 2)
        cmd(ser, "AT+GMM", 2)
        cmd(ser, "AT+GMR", 2)

        # Detectar tono de marcado (debe fallar con NO DIALTONE si no hay línea)
        logging.info("--- Prueba tono (ATX4 + ATDT,) ---")
        cmd(ser, "ATX4")
        # ATDL o marcar con coma espera tono; ATDT; solo coge línea
        r = cmd(ser, "ATX4;DT,", wait=8)  # invalid maybe
        cmd(ser, "ATH")

        # Off-hook y escuchar resultado
        logging.info("--- Descolgar (ATH1 / ATA data) ---")
        cmd(ser, "ATX4")
        r1 = cmd(ser, "ATH1", wait=6)
        time.sleep(2)
        cmd(ser, "ATH")

        logging.info("--- Marcar con espera de tono ATDT (sin ; voz) a número corto inválido ---")
        # Un solo dígito: si no hay línea -> NO DIALTONE
        cmd(ser, "AT+FCLASS=0")
        cmd(ser, "ATX4")
        r2 = cmd(ser, "ATDT1;", wait=12)
        cmd(ser, "ATH")

        logging.info("--- Marcar ciego ATX0 (sin comprobar tono) ---")
        cmd(ser, "ATX0")
        r3 = cmd(ser, "ATDT123;", wait=15)
        cmd(ser, "ATH")

        logging.info("--- Voz: FCLASS=8, VLS, dial tono ---")
        cmd(ser, "AT+FCLASS=8")
        cmd(ser, "AT+VLS=0")
        cmd(ser, "ATX4")
        # En voz, a veces hay que descolgar con VLS primero
        cmd(ser, "AT+VLS=1")
        r4 = cmd(ser, "ATD,", wait=10)  # wait dialtone in voice?
        cmd(ser, "ATH")
        cmd(ser, "AT+VLS=0")

        logging.info("--- AT&V / perfiles ---")
        cmd(ser, "AT&V", wait=4)

        print("\n=== RESUMEN ===")
        print("Si ves NO DIALTONE: el RJ11 no tiene línea PSTN (cable desconectado o solo USB).")
        print("Si ATH1/ATDT dan OK rápido sin NO DIALTONE ni BUSY: puede haber línea o el módem ignora tono.")
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass
        subprocess.call(["systemctl", "start", "zeromatic.service"])


if __name__ == "__main__":
    raise SystemExit(main())
