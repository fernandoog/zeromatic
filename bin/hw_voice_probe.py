#!/usr/bin/env python3
"""Probe profundo de capacidades de voz del módem."""
import subprocess
import time

import serial

DEVICE = "/dev/ttyACM0"


def cmd(ser, c, w=4):
    ser.reset_input_buffer()
    ser.write((c + "\r").encode())
    ser.flush()
    end = time.time() + w
    buf = b""
    while time.time() < end:
        ch = ser.read(256)
        if ch:
            buf += ch
            u = buf.upper()
            if any(
                x in u
                for x in (b"OK", b"ERROR", b"CONNECT", b"VCON", b"NO CARRIER", b"NO DIAL")
            ):
                time.sleep(0.15)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    t = buf.decode("ascii", errors="ignore")
    print(f">>> {c} | <<< {t!r}")
    return t


def main():
    subprocess.call(["systemctl", "stop", "buzonera.service"])
    time.sleep(2)
    ser = serial.Serial(DEVICE, 115200, timeout=0.3, rtscts=True)
    time.sleep(0.4)
    try:
        cmd(ser, "ATH")
        cmd(ser, "ATZ")
        cmd(ser, "ATE0")
        print("--- voice deeper ---")
        cmd(ser, "AT+FCLASS=8")
        for c in [
            "AT+VIP",
            "AT+VIT=?",
            "AT+VRA=?",
            "AT+VRN=?",
            "AT+VTD=?",
            "AT+VSD=?",
            "AT+VSM=?",
            "AT+VSM?",
            "AT+VSM=1",
            "AT+VSM=1,8000",
            "AT+VSM=1,8000,0,0",
            "AT+VSM=129,8000",
            "AT+VSM=130,8000",
            "AT+VLS=?",
            "AT+VTX",
            "ATH",
            "AT+FCLASS=8",
            "AT+VLS=1",
            "AT+VSM=1,8000,0,0",
            "AT+VRX",
            "ATH",
            "AT+FCLASS=0",
        ]:
            cmd(ser, c, 3)

        print("--- dialtone ATX4 ATDT; ---")
        cmd(ser, "ATX4")
        cmd(ser, "ATDT;", 8)
        cmd(ser, "ATH")

        print("--- voltage / line monitor if any ---")
        for c in ["ATI11", "AT%V", "AT%L", "AT&V1"]:
            cmd(ser, c, 3)
        print("DONE")
    finally:
        ser.close()
        subprocess.call(["systemctl", "start", "buzonera.service"])
        time.sleep(1)
        print("buzonera:", subprocess.getoutput("systemctl is-active buzonera"))


if __name__ == "__main__":
    main()
