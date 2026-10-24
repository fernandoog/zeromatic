#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/buzonera_daemon.py
install -m 755 /tmp/buzonera_daemon.py /opt/buzonera/bin/buzonera_daemon.py
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/buzonera_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY
systemctl restart buzonera
sleep 3
systemctl is-active buzonera
journalctl -u buzonera -n 10 --no-pager
