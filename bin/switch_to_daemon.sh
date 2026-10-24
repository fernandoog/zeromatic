#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/buzonera_up/* || true
install -m 755 /tmp/buzonera_up/buzonera_daemon.py /opt/buzonera/bin/
install -m 755 /tmp/buzonera_up/on_message.sh /opt/buzonera/bin/
install -m 755 /tmp/buzonera_up/test_pipeline.sh /opt/buzonera/bin/
install -m 755 /tmp/buzonera_up/make_greeting_wav.sh /opt/buzonera/bin/
install -m 644 /tmp/buzonera_up/buzonera.service /etc/systemd/system/buzonera.service
mkdir -p /opt/buzonera/media /var/spool/voice/incoming /var/spool/voice/processed
sed -i '1s|.*|#!/opt/buzonera/venv/bin/python3|' /opt/buzonera/bin/buzonera_daemon.py
sed -i '1s|.*|#!/opt/buzonera/venv/bin/python3|' /opt/buzonera/bin/transcribe.py
sed -i '1s|.*|#!/opt/buzonera/venv/bin/python3|' /opt/buzonera/bin/send_mail.py
/opt/buzonera/venv/bin/pip install -q pyserial SpeechRecognition
/opt/buzonera/bin/make_greeting_wav.sh
systemctl stop buzonera.service || true
pkill -f '/usr/sbin/vgetty' || true
systemctl daemon-reload
systemctl enable buzonera.service
systemctl restart buzonera.service
sleep 3
systemctl --no-pager -l status buzonera.service
