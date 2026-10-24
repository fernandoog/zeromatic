#!/usr/bin/env python3
import subprocess, time, serial

subprocess.call(["systemctl", "stop", "zeromatic.service"])
time.sleep(1)
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.25, rtscts=True)
time.sleep(0.4)

def cmd(c, w=4):
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
            if any(x in u for x in (b"OK", b"ERROR", b"NO DIAL", b"NO CARRIER", b"BUSY", b"CONNECT")):
                time.sleep(0.15)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    t = buf.decode("ascii", errors="ignore").replace("\r", "\\r").replace("\n", "\\n")
    print(f">>> {c} | <<< {t}")
    return t

try:
    cmd("ATH")
    cmd("ATZ")
    cmd("ATE0")
    cmd("ATQ0V1")
    cmd("ATI3")
    cmd("AT+FCLASS=0")
    cmd("ATX4")
    print("--- tono ---")
    r = cmd("ATDTW,", 10)
    cmd("ATH")
    print("--- marcar digito con tono ---")
    r2 = cmd("ATDT1", 15)
    cmd("ATH")
    print("RESULT_DIALTONE_TEST:", "NO_DIALTONE" if "NO DIAL" in (r + r2).upper() else "OTHER")
finally:
    ser.close()
    subprocess.call(["systemctl", "start", "zeromatic.service"])
