#!/bin/bash
set -eu
tr -d '\r' < /tmp/zeromatic_daemon.py > /tmp/daemon.lf.py
install -m 755 /tmp/daemon.lf.py /opt/zeromatic/bin/zeromatic_daemon.py
if [[ -f /tmp/zeromatic.service ]]; then
  tr -d '\r' < /tmp/zeromatic.service > /tmp/zeromatic.service.lf
  install -m 644 /tmp/zeromatic.service.lf /etc/systemd/system/zeromatic.service
  systemctl daemon-reload
fi
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/zeromatic/bin/zeromatic_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/zeromatic/venv/bin/python3\n"
p.write_text("".join(lines))
PY
systemctl restart zeromatic
sleep 10
systemctl is-active zeromatic
journalctl -u zeromatic -n 12 --no-pager
fuser -v /dev/ttyACM0 2>&1 || true
