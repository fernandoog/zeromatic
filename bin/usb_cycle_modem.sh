#!/bin/bash
set -eu
systemctl stop buzonera.service 2>/dev/null || true
pkill -9 -f buzonera_daemon.py 2>/dev/null || true
sleep 1

echo "Before:"
lsusb
ls -l /dev/ttyACM* 2>/dev/null || echo no-acm

# Find Conexant device path (NOT the root hub)
for d in /sys/bus/usb/devices/*; do
  [[ -f "$d/idVendor" ]] || continue
  v=$(cat "$d/idVendor")
  p=$(cat "$d/idProduct")
  if [[ "$v" == "0572" && "$p" == "1340" ]]; then
    echo "Found modem at $d"
    if [[ -f "$d/authorized" ]]; then
      echo 0 > "$d/authorized"
      sleep 2
      echo 1 > "$d/authorized"
      echo "cycled authorized"
    fi
    # also try reset
    if [[ -f "$d/reset" ]]; then
      echo 0 > "$d/reset" || true
      echo "reset issued"
    fi
  fi
done

sleep 4
echo "After:"
lsusb
ls -l /dev/ttyACM* 2>/dev/null || echo no-acm
dmesg | tail -15

if [[ -c /dev/ttyACM0 ]]; then
  /opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.5, rtscts=True)
time.sleep(1.5)
ser.dtr = True
time.sleep(0.3)
ser.reset_input_buffer()
ser.write(b"AT\r")
ser.flush()
time.sleep(1)
print("AT ->", ser.read(64))
ser.write(b"ATI3\r")
ser.flush()
time.sleep(1)
print("ATI3 ->", ser.read(128))
ser.close()
PY
fi
