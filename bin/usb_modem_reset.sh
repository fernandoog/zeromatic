#!/bin/bash
# Reset USB del módem Conexant + redeploy daemon
set -euo pipefail
systemctl stop buzonera.service || true
pkill -9 -f buzonera_daemon.py 2>/dev/null || true
sleep 1

# Reset AT si el nodo existe
if [[ -c /dev/ttyACM0 ]]; then
  /opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.4, rtscts=True)
time.sleep(0.4)
for c in [b"\x10\x03", b"AT+VLS=0\r", b"ATH\r", b"AT+FCLASS=0\r", b"ATH\r", b"ATZ\r", b"ATE0\r", b"ATS0=0\r"]:
    ser.write(c); ser.flush(); time.sleep(0.5)
    print(c, "->", ser.read(512))
ser.close()
print("AT hangup done")
PY
fi

# Rebind USB (fuerza rediscovery cdc_acm)
DEV=$(lsusb | awk '/Conexant|0572:1340/{print $2"/"$4}' | tr -d ':' | head -1 || true)
# Prefer sysfs path from ttyACM0
USBPATH=$(readlink -f /sys/class/tty/ttyACM0/device/../.. 2>/dev/null || true)
if [[ -n "${USBPATH}" && -e "${USBPATH}/authorized" ]]; then
  echo 0 > "${USBPATH}/authorized" || true
  sleep 1
  echo 1 > "${USBPATH}/authorized" || true
  echo "USB authorized toggle: ${USBPATH}"
  sleep 3
else
  echo "WARN: no USB path for authorized toggle"
fi

# Esperar ttyACM0
for i in $(seq 1 15); do
  [[ -c /dev/ttyACM0 ]] && break
  sleep 0.5
done
ls -l /dev/ttyACM0
lsusb | grep -i conexant || true

# Verificar RING path
/opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.3, rtscts=True)
time.sleep(0.4)
def cmd(c, w=2):
    ser.reset_input_buffer()
    ser.write((c+"\r").encode()); ser.flush()
    time.sleep(w)
    r = ser.read(ser.in_waiting or 0)
    print(f">>> {c} <<< {r!r}")
    return r
cmd("ATZ", 3)
cmd("ATE0")
cmd("ATQ0V1")
cmd("ATI3")
cmd("AT+FCLASS=0")
cmd("ATS0=1")
cmd("AT+VCID=1")
print("modem ready for RING/autoanswer")
ser.close()
PY

install -m 755 /tmp/buzonera_daemon.py /opt/buzonera/bin/buzonera_daemon.py
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/buzonera_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY

systemctl start buzonera.service
sleep 5
systemctl is-active buzonera
journalctl -u buzonera -n 15 --no-pager
fuser -v /dev/ttyACM0 2>&1 || true
