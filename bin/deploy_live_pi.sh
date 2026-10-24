#!/bin/bash
# Despliega el daemon a la instalación en vivo (zeromatic / legado).
set -eu
SRC=/tmp/zeromatic_daemon.py
if [[ -d /opt/zeromatic ]]; then
  DEST=/opt/zeromatic/bin/zeromatic_daemon.py
  VENV=/opt/zeromatic/venv/bin/python3
  SVC=zeromatic
elif [[ -d /opt/buzonera ]]; then
  DEST=/opt/buzonera/bin/buzonera_daemon.py
  VENV=/opt/buzonera/venv/bin/python3
  SVC=buzonera
elif [[ -d /opt/contestador ]]; then
  DEST=/opt/contestador/bin/contestador_daemon.py
  VENV=/opt/contestador/venv/bin/python3
  SVC=contestador
else
  echo "No hay /opt/zeromatic, /opt/buzonera ni /opt/contestador"
  exit 1
fi
tr -d '\r' < "$SRC" > /tmp/daemon.lf.py
install -m 755 /tmp/daemon.lf.py "$DEST"
python3 - <<PY
from pathlib import Path
p = Path("$DEST")
lines = p.read_text().splitlines(True)
lines[0] = "#!$VENV\n"
p.write_text("".join(lines))
PY
systemctl restart "$SVC"
sleep 8
systemctl is-active "$SVC"
journalctl -u "$SVC" -n 10 --no-pager
