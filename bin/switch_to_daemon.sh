#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/contestador_up/* || true
install -m 755 /tmp/contestador_up/contestador_daemon.py /opt/contestador/bin/
install -m 755 /tmp/contestador_up/on_message.sh /opt/contestador/bin/
install -m 755 /tmp/contestador_up/test_pipeline.sh /opt/contestador/bin/
install -m 755 /tmp/contestador_up/make_greeting_wav.sh /opt/contestador/bin/
install -m 644 /tmp/contestador_up/contestador.service /etc/systemd/system/contestador.service
mkdir -p /opt/contestador/media /var/spool/voice/incoming /var/spool/voice/processed
sed -i '1s|.*|#!/opt/contestador/venv/bin/python3|' /opt/contestador/bin/contestador_daemon.py
sed -i '1s|.*|#!/opt/contestador/venv/bin/python3|' /opt/contestador/bin/transcribe.py
sed -i '1s|.*|#!/opt/contestador/venv/bin/python3|' /opt/contestador/bin/send_mail.py
/opt/contestador/venv/bin/pip install -q pyserial SpeechRecognition
/opt/contestador/bin/make_greeting_wav.sh
systemctl stop contestador.service || true
pkill -f '/usr/sbin/vgetty' || true
systemctl daemon-reload
systemctl enable contestador.service
systemctl restart contestador.service
sleep 3
systemctl --no-pager -l status contestador.service
