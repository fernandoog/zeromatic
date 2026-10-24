#!/bin/bash
# Diagnóstico rápido de tono de línea. Ejecutar con sudo (o usuario en dialout + control del servicio).
set -euo pipefail

systemctl stop zeromatic.service
sleep 1

/opt/zeromatic/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.25, rtscts=True)
time.sleep(0.4)

def cmd(c, w=5):
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
    t = buf.decode("ascii", "ignore").replace("\r", "\\r").replace("\n", "\\n")
    print(f">>> {c} | <<< {t}")
    return t

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
print("--- digito ---")
r2 = cmd("ATDT1", 12)
cmd("ATH")
combo = (r + r2).upper()
if "NO DIAL" in combo:
    print("VERDICT: NO_DIALTONE — sin linea telefonica en el puerto LINE")
elif "BUSY" in combo:
    print("VERDICT: BUSY — hay red")
else:
    print("VERDICT: OTHER", combo[:180])
ser.close()
PY

systemctl start zeromatic.service
systemctl is-active zeromatic.service
