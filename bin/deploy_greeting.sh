#!/bin/bash
set -eu
# Backup espeak actual si aún no hay recuerdo en la Pi
if [[ -f /opt/buzonera/media/greeting.wav && ! -f /opt/buzonera/media/greeting_espeak_recuerdo.wav ]]; then
  cp -a /opt/buzonera/media/greeting.wav /opt/buzonera/media/greeting_espeak_recuerdo.wav
fi
install -m 644 /tmp/greeting.wav /opt/buzonera/media/greeting.wav
if [[ -f /tmp/greeting_espeak_recuerdo.wav ]]; then
  install -m 644 /tmp/greeting_espeak_recuerdo.wav /opt/buzonera/media/greeting_espeak_recuerdo.wav
fi
if [[ -f /tmp/make_greeting_wav.sh ]]; then
  tr -d '\r' < /tmp/make_greeting_wav.sh > /opt/buzonera/bin/make_greeting_wav.sh
  chmod 755 /opt/buzonera/bin/make_greeting_wav.sh
fi
ls -la /opt/buzonera/media/
systemctl restart buzonera
sleep 8
systemctl is-active buzonera
journalctl -u buzonera -n 8 --no-pager
