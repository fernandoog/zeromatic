#!/bin/bash
set -eu
systemctl stop buzonera.service 2>/dev/null || true
pkill -9 -f buzonera_daemon.py 2>/dev/null || true
sleep 1

echo "=== USB before ==="
lsusb || true
ls -l /dev/ttyACM* 2>/dev/null || echo "no ttyACM"

# Re-authorize any Conexant device
for d in /sys/bus/usb/devices/*; do
  if [[ -f "$d/idVendor" ]]; then
    v=$(cat "$d/idVendor" 2>/dev/null || true)
    p=$(cat "$d/idProduct" 2>/dev/null || true)
    if [[ "$v" == "0572" ]]; then
      echo "Conexant at $d ($v:$p)"
      if [[ -f "$d/authorized" ]]; then
        echo 0 > "$d/authorized" || true
        sleep 1
        echo 1 > "$d/authorized" || true
        echo "toggled authorized on $d"
      fi
    fi
  fi
done

# Also try rebinding cdc_acm
if [[ -d /sys/bus/usb/drivers/cdc_acm ]]; then
  for link in /sys/bus/usb/drivers/cdc_acm/*; do
    base=$(basename "$link")
    [[ "$base" == "module" || "$base" == "bind" || "$base" == "unbind" || "$base" == "uevent" ]] && continue
    if [[ -e "$link" ]]; then
      echo "rebind cdc_acm $base"
      echo "$base" > /sys/bus/usb/drivers/cdc_acm/unbind 2>/dev/null || true
      sleep 1
      echo "$base" > /sys/bus/usb/drivers/cdc_acm/bind 2>/dev/null || true
    fi
  done
fi

sleep 3
echo "=== USB after ==="
lsusb | grep -iE 'conexant|0572' || lsusb
ls -l /dev/ttyACM* 2>/dev/null || echo "STILL no ttyACM"

if [[ ! -c /dev/ttyACM0 ]]; then
  echo "Trying usbreset via python..."
  # Last resort: reload module
  modprobe -r cdc_acm 2>/dev/null || true
  sleep 1
  modprobe cdc_acm 2>/dev/null || true
  sleep 3
  ls -l /dev/ttyACM* 2>/dev/null || echo "FAIL: modem missing — unplug/replug USB"
  exit 1
fi

# Exit voice stream if stuck, then reset
/opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.2, rtscts=True)
time.sleep(0.3)
# Flood DLE+ETX to leave VRX/VTX
for _ in range(5):
    ser.write(bytes([0x10, 0x03]))
    ser.flush()
    time.sleep(0.15)
# Drain PCM garbage
t0 = time.time()
n = 0
while time.time() - t0 < 2.0:
    chunk = ser.read(4096)
    if not chunk:
        break
    n += len(chunk)
print(f"drained {n} bytes")
time.sleep(0.3)
ser.reset_input_buffer()
for c in ["ATH", "AT+VLS=0", "ATH", "AT+FCLASS=0", "ATZ", "ATE0", "ATQ0V1", "ATI3", "ATS0=1", "AT+VCID=1"]:
    ser.reset_input_buffer()
    ser.write((c + "\r").encode())
    ser.flush()
    time.sleep(0.8 if c == "ATZ" else 0.35)
    r = ser.read(512)
    print(f">>> {c} <<< {r!r}")
ser.close()
print("modem command mode OK")
PY

# Deploy daemon
if [[ -f /tmp/buzonera_daemon.py ]]; then
  tr -d '\r' < /tmp/buzonera_daemon.py > /tmp/daemon.lf.py
  install -m 755 /tmp/daemon.lf.py /opt/buzonera/bin/buzonera_daemon.py
  python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/buzonera_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY
fi

systemctl start buzonera.service
sleep 6
systemctl is-active buzonera
journalctl -u buzonera -n 12 --no-pager
fuser -v /dev/ttyACM0 2>&1 || true
