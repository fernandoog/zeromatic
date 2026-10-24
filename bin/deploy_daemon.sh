#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/contestador_daemon.py /tmp/make_greeting_wav.sh /tmp/contestador.service 2>/dev/null || true
install -m 755 /tmp/contestador_daemon.py /opt/contestador/bin/contestador_daemon.py
install -m 755 /tmp/make_greeting_wav.sh /opt/contestador/bin/make_greeting_wav.sh
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/contestador/bin/contestador_daemon.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/contestador/venv/bin/python3\n"
p.write_text("".join(lines))
PY
/opt/contestador/bin/make_greeting_wav.sh
sox --i /opt/contestador/media/greeting.wav || true
systemctl stop contestador || true
pkill -9 -f contestador_daemon.py 2>/dev/null || true
sleep 1
systemctl start contestador
sleep 3
systemctl is-active contestador
journalctl -u contestador -n 12 --no-pager
