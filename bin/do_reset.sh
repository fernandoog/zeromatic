#!/bin/bash
set -eu
systemctl stop buzonera.service || true
sleep 1
pkill -9 -f buzonera_daemon.py || true
sleep 1
tr -d '\r' < /tmp/hard_reset_modem.py > /tmp/hr.lf.py
/opt/buzonera/venv/bin/python3 /tmp/hr.lf.py
tr -d '\r' < /tmp/install_daemon_only.sh > /tmp/i.lf.sh
bash /tmp/i.lf.sh
