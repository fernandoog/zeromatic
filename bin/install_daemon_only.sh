#!/bin/bash
set -eu
tr -d '\r' < /tmp/buzonera_daemon.py > /tmp/daemon.lf.py
install -m 755 /tmp/daemon.lf.py /opt/buzonera/bin/buzonera_daemon.py
if [[ -f /tmp/buzonera.service ]]; then
  tr -d '\r' < /tmp/buzonera.service > /tmp/buzonera.service.lf
  install -m 644 /tmp/buzonera.service.lf /etc/systemd/system/buzonera.service
  systemctl daemon-reload
fi
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/buzonera_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY
systemctl restart buzonera
sleep 10
systemctl is-active buzonera
journalctl -u buzonera -n 12 --no-pager
fuser -v /dev/ttyACM0 2>&1 || true
