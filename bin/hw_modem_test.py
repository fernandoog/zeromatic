#!/usr/bin/env python3
"""Test de hardware del módem USB (Conexant CX93010)."""
from __future__ import annotations

import subprocess
import sys
import time

import serial

DEVICE = "/dev/ttyACM0"
BAUD = 115200


def cmd(ser: serial.Serial, command: str, wait: float = 5.0) -> str:
    ser.reset_input_buffer()
    ser.write((command + "\r").encode())
    ser.flush()
    end = time.time() + wait
    buf = b""
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            u = buf.upper()
            if any(
                x in u
                for x in (
                    b"OK",
                    b"ERROR",
                    b"NO DIAL",
                    b"NO CARRIER",
                    b"BUSY",
                    b"CONNECT",
                    b"VCON",
                    b"RING",
                )
            ):
                time.sleep(0.2)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    text = buf.decode("ascii", errors="ignore")
    clean = text.replace("\r", "\\r").replace("\n", "\\n")
    print(f">>> {command}")
    print(f"<<< {clean}")
    return text


def has_nodial(*parts: str) -> bool:
    return "NO DIAL" in "".join(parts).upper()


def main() -> int:
    print("======== HARDWARE MODEM TEST ========")
    subprocess.call(["systemctl", "stop", "buzonera.service"])
    time.sleep(2)

    try:
        ser = serial.Serial(DEVICE, BAUD, timeout=0.3, rtscts=True)
    except Exception as e:
        print(f"FAIL open {DEVICE}: {e}")
        subprocess.call(["systemctl", "start", "buzonera.service"])
        return 1

    time.sleep(0.5)
    try:
        print("--- Identificacion ---")
        cmd(ser, "ATH")
        cmd(ser, "ATZ", 4)
        cmd(ser, "ATE0")
        cmd(ser, "ATQ0V1")
        for c in (
            "ATI",
            "ATI0",
            "ATI1",
            "ATI2",
            "ATI3",
            "ATI4",
            "ATI5",
            "ATI6",
            "ATI7",
            "AT+GMI",
            "AT+GMM",
            "AT+GMR",
            "AT+GSN",
            "AT+FMI?",
            "AT+FMM?",
            "AT+FMR?",
        ):
            cmd(ser, c, 3)

        print("--- Capacidades voz/fax/data ---")
        for c in (
            "AT+FCLASS=?",
            "AT+VCID=?",
            "AT+VLS=?",
            "AT+VSM=?",
            "AT+VGR=?",
            "AT+VGT=?",
        ):
            cmd(ser, c, 3)

        print("--- Perfil ---")
        cmd(ser, "AT&V", 4)

        print("--- Test tono (ATX4 + ATDTW,) ---")
        cmd(ser, "AT+FCLASS=0")
        cmd(ser, "ATX4")
        r_tone = cmd(ser, "ATDTW,", 12)
        cmd(ser, "ATH")

        print("--- Test digito con deteccion de tono ---")
        r_digit = cmd(ser, "ATDT1", 15)
        cmd(ser, "ATH")

        print("--- Off-hook ATH1 ---")
        r_hook = cmd(ser, "ATH1", 6)
        time.sleep(1.5)
        cmd(ser, "ATH")

        print("--- Modo voz: FCLASS=8 VLS dialtone ---")
        cmd(ser, "AT+FCLASS=8")
        cmd(ser, "AT+VLS=0")
        cmd(ser, "AT+VLS=1", 4)
        r_voice = cmd(ser, "ATD,", 10)
        cmd(ser, "ATH")
        cmd(ser, "AT+VLS=0")
        cmd(ser, "AT+FCLASS=0")

        print("--- Caller ID capability ---")
        cmd(ser, "AT+VCID=1", 3)
        cmd(ser, "AT+VCID=0", 3)

        print("\n======== RESUMEN ========")
        print("USB: 0572:1340 Conexant CX93010 ACF Modem")
        print("DEVICE:", DEVICE)
        if has_nodial(r_tone, r_digit, r_hook, r_voice):
            print("VERDICT: NO_DIALTONE (cable RJ11 / linea PSTN)")
            print("MODEM_HW: responde AT OK (chip OK); fallo es de linea, no del USB")
            code = 2
        else:
            print("VERDICT: LINE_OK_OR_NO_NODIALTONE")
            print("MODEM_HW: chip + linea parecen OK")
            code = 0
        return code
    finally:
        try:
            ser.close()
        except Exception:
            pass
        subprocess.call(["systemctl", "start", "buzonera.service"])
        time.sleep(1)
        print("buzonera:", subprocess.getoutput("systemctl is-active buzonera"))


if __name__ == "__main__":
    raise SystemExit(main())
