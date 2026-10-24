#!/bin/bash
set -eu
# Backup espeak actual si aún no hay recuerdo en la Pi
if [[ -f /opt/zeromatic/media/greeting.wav && ! -f /opt/zeromatic/media/greeting_espeak_recuerdo.wav ]]; then
  cp -a /opt/zeromatic/media/greeting.wav /opt/zeromatic/media/greeting_espeak_recuerdo.wav
fi
install -m 644 /tmp/greeting.wav /opt/zeromatic/media/greeting.wav
if [[ -f /tmp/greeting_espeak_recuerdo.wav ]]; then
  install -m 644 /tmp/greeting_espeak_recuerdo.wav /opt/zeromatic/media/greeting_espeak_recuerdo.wav
fi
if [[ -f /tmp/make_greeting_wav.sh ]]; then
  tr -d '\r' < /tmp/make_greeting_wav.sh > /opt/zeromatic/bin/make_greeting_wav.sh
  chmod 755 /opt/zeromatic/bin/make_greeting_wav.sh
fi
ls -la /opt/zeromatic/media/
systemctl restart zeromatic
sleep 8
systemctl is-active zeromatic
journalctl -u zeromatic -n 8 --no-pager
