#!/bin/bash
# Ejecutar en la Pi como root (o con sudo)
set -euo pipefail

SRC="${1:-/home/pi/buzonera}"
if [[ ! -d "$SRC/bin" ]]; then
  echo "No encuentro $SRC/bin"
  exit 1
fi

install -d -m 755 /opt/buzonera/bin /opt/buzonera/media /etc/buzonera \
  /var/spool/voice/incoming /var/spool/voice/messages /var/spool/voice/processed
install -m 755 "$SRC"/bin/*.sh /opt/buzonera/bin/ 2>/dev/null || true
install -m 755 "$SRC"/bin/*.py /opt/buzonera/bin/
install -m 644 "$SRC"/requirements.txt /opt/buzonera/requirements.txt 2>/dev/null || true
if [[ -d /etc/mgetty ]]; then
  install -m 644 "$SRC"/etc/voice.conf /etc/mgetty/voice.conf
fi
install -m 644 "$SRC"/systemd/buzonera.service /etc/systemd/system/buzonera.service

if [[ ! -f /etc/buzonera/email.env ]]; then
  install -m 600 "$SRC"/etc/email.env.example /etc/buzonera/email.env
  echo "EDITAR: /etc/buzonera/email.env (GMAIL_APP_PASSWORD)"
fi

# Grupo voice + permisos
getent group voice >/dev/null || groupadd voice
usermod -aG voice,dialout "${SUDO_USER:-pi}" || true
chown -R root:voice /var/spool/voice
chmod 775 /var/spool/voice/incoming /var/spool/voice/messages /var/spool/voice/processed
touch /var/log/buzonera.log
chown root:voice /var/log/buzonera.log
chmod 664 /var/log/buzonera.log

# mgetty.config — puerto USB
if ! grep -q 'port ttyACM0' /etc/mgetty/mgetty.config; then
  cat >>/etc/mgetty/mgetty.config <<'EOF'

# Buzonera — módem USB Conexant
port ttyACM0
  speed 115200
  data-only n
  toggle-dtr y
EOF
fi
# Subir velocidad global si está en 38400
sed -i 's/^speed 38400/speed 115200/' /etc/mgetty/mgetty.config || true

# Locución
/opt/buzonera/bin/make_greeting.sh

# Dependencias de sistema (idempotente)
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq sox espeak-ng flac ffmpeg python3-venv python3-pip >/dev/null || true

# Python deps (venv ligero)
if [[ ! -d /opt/buzonera/venv ]]; then
  python3 -m venv /opt/buzonera/venv
fi
/opt/buzonera/venv/bin/pip install -q --upgrade pip
if [[ -f "$SRC/requirements.txt" ]]; then
  /opt/buzonera/venv/bin/pip install -q -r "$SRC/requirements.txt"
else
  /opt/buzonera/venv/bin/pip install -q pyserial SpeechRecognition openai
fi

install -d -m 755 /opt/buzonera/media

# Apuntar scripts a venv
for py in buzonera_daemon.py transcribe.py send_mail.py test_call.py \
          diag_line.py diag_quick.py hw_modem_test.py hw_voice_probe.py test_ring.py; do
  if [[ -f /opt/buzonera/bin/$py ]]; then
    sed -i '1s|.*|#!/opt/buzonera/venv/bin/python3|' "/opt/buzonera/bin/$py"
  fi
done

# Desactivar getty serial si existe
systemctl stop serial-getty@ttyACM0.service 2>/dev/null || true
systemctl disable serial-getty@ttyACM0.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable buzonera.service
systemctl restart buzonera.service
sleep 2
systemctl --no-pager -l status buzonera.service || true
echo "Instalación lista. Configura /etc/buzonera/email.env y prueba con /opt/buzonera/bin/test_pipeline.sh"
