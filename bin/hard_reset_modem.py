#!/usr/bin/env python3
"""Reset duro del módem: drenar PCM, colgar, comprobar AT OK."""
import serial
import time
import sys

PORT = "/dev/ttyACM0"


def main() -> int:
    ser = serial.Serial(PORT, 115200, timeout=0.25, rtscts=True)
    time.sleep(0.4)
    # salir de VTX/VRX
    for _ in range(8):
        ser.write(bytes([0x10, 0x03]))
        ser.flush()
        time.sleep(0.05)
    t0 = time.time()
    drained = 0
    while time.time() - t0 < 2.5:
        chunk = ser.read(4096)
        if not chunk:
            break
        drained += len(chunk)
    print(f"drained {drained} bytes")
    ser.reset_input_buffer()
    time.sleep(0.3)

    def cmd(c, w=2.0):
        ser.reset_input_buffer()
        ser.write((c + "\r").encode())
        ser.flush()
        end = time.time() + w
        buf = b""
        while time.time() < end:
            ch = ser.read(256)
            if ch:
                buf += ch
                if b"OK" in buf or b"ERROR" in buf:
                    time.sleep(0.1)
                    buf += ser.read(ser.in_waiting or 0)
                    break
            else:
                time.sleep(0.02)
        print(f">>> {c} <<< {buf!r}")
        return buf

    for c in ["ATH", "ATZ", "ATE0", "ATQ0V1", "ATI3", "AT+FCLASS=0", "AT+FCLASS?", "ATS0=0"]:
        cmd(c, 3.0 if c == "ATZ" else 1.5)

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
