#!/bin/bash
# Reset duro del módem + redeploy daemon + flac + permisos email.env
set -euo pipefail
sed -i 's/\r$//' /tmp/buzonera_daemon.py /tmp/fix_and_restart.sh 2>/dev/null || true

systemctl stop buzonera.service || true
pkill -9 -f buzonera_daemon.py 2>/dev/null || true
sleep 1

# Liberar línea / reset USB ACM
if [[ -c /dev/ttyACM0 ]]; then
  /opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
try:
    ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.3, rtscts=True)
    time.sleep(0.3)
    for c in (b"ATH\r", b"AT+VLS=0\r", b"ATH\r", b"AT+FCLASS=0\r", b"ATS0=0\r", b"ATZ\r"):
        ser.write(c); ser.flush(); time.sleep(0.4)
        ser.read(ser.in_waiting or 0)
    ser.close()
    print("modem reset OK")
except Exception as e:
    print("modem reset WARN:", e)
PY
fi

install -m 755 /tmp/buzonera_daemon.py /opt/buzonera/bin/buzonera_daemon.py
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/buzonera_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY

apt-get install -y -qq flac >/dev/null 2>&1 || true
chmod 640 /etc/buzonera/email.env 2>/dev/null || true
chown root:root /etc/buzonera/email.env 2>/dev/null || true

systemctl start buzonera.service
sleep 4
systemctl is-active buzonera
journalctl -u buzonera -n 12 --no-pager
fuser /dev/ttyACM0 2>&1 || true
