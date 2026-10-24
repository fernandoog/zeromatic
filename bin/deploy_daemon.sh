#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/zeromatic_daemon.py
install -m 755 /tmp/zeromatic_daemon.py /opt/zeromatic/bin/zeromatic_daemon.py
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/zeromatic/bin/zeromatic_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/zeromatic/venv/bin/python3\n"
p.write_text("".join(lines))
PY
systemctl restart zeromatic
sleep 3
systemctl is-active zeromatic
journalctl -u zeromatic -n 10 --no-pager
