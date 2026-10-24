#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/zeromatic_up/* || true
install -m 755 /tmp/zeromatic_up/zeromatic_daemon.py /opt/zeromatic/bin/
install -m 755 /tmp/zeromatic_up/on_message.sh /opt/zeromatic/bin/
install -m 755 /tmp/zeromatic_up/test_pipeline.sh /opt/zeromatic/bin/
install -m 755 /tmp/zeromatic_up/make_greeting_wav.sh /opt/zeromatic/bin/
install -m 644 /tmp/zeromatic_up/zeromatic.service /etc/systemd/system/zeromatic.service
mkdir -p /opt/zeromatic/media /var/spool/voice/incoming /var/spool/voice/processed
sed -i '1s|.*|#!/opt/zeromatic/venv/bin/python3|' /opt/zeromatic/bin/zeromatic_daemon.py
sed -i '1s|.*|#!/opt/zeromatic/venv/bin/python3|' /opt/zeromatic/bin/transcribe.py
sed -i '1s|.*|#!/opt/zeromatic/venv/bin/python3|' /opt/zeromatic/bin/send_mail.py
/opt/zeromatic/venv/bin/pip install -q pyserial SpeechRecognition
/opt/zeromatic/bin/make_greeting_wav.sh
systemctl stop zeromatic.service || true
pkill -f '/usr/sbin/vgetty' || true
systemctl daemon-reload
systemctl enable zeromatic.service
systemctl restart zeromatic.service
sleep 3
systemctl --no-pager -l status zeromatic.service
