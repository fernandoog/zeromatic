#!/bin/bash
# Ejecutar en la Pi como root (o con sudo)
set -euo pipefail

SRC="${1:-/home/pi/zeromatic}"
if [[ ! -d "$SRC/bin" ]]; then
  echo "No encuentro $SRC/bin"
  exit 1
fi

install -d -m 755 /opt/zeromatic/bin /opt/zeromatic/media /etc/zeromatic \
  /var/spool/voice/incoming /var/spool/voice/messages /var/spool/voice/processed
install -m 755 "$SRC"/bin/*.sh /opt/zeromatic/bin/ 2>/dev/null || true
install -m 755 "$SRC"/bin/*.py /opt/zeromatic/bin/
install -m 644 "$SRC"/requirements.txt /opt/zeromatic/requirements.txt 2>/dev/null || true
if [[ -d /etc/mgetty ]]; then
  install -m 644 "$SRC"/etc/voice.conf /etc/mgetty/voice.conf
fi
install -m 644 "$SRC"/systemd/zeromatic.service /etc/systemd/system/zeromatic.service

if [[ ! -f /etc/zeromatic/email.env ]]; then
  install -m 600 "$SRC"/etc/email.env.example /etc/zeromatic/email.env
  echo "EDITAR: /etc/zeromatic/email.env (GMAIL_APP_PASSWORD)"
fi

# Grupo voice + permisos
getent group voice >/dev/null || groupadd voice
usermod -aG voice,dialout "${SUDO_USER:-pi}" || true
chown -R root:voice /var/spool/voice
chmod 775 /var/spool/voice/incoming /var/spool/voice/messages /var/spool/voice/processed
touch /var/log/zeromatic.log
chown root:voice /var/log/zeromatic.log
chmod 664 /var/log/zeromatic.log

# mgetty.config — puerto USB
if ! grep -q 'port ttyACM0' /etc/mgetty/mgetty.config; then
  cat >>/etc/mgetty/mgetty.config <<'EOF'

# Zeromatic — módem USB Conexant
port ttyACM0
  speed 115200
  data-only n
  toggle-dtr y
EOF
fi
# Subir velocidad global si está en 38400
sed -i 's/^speed 38400/speed 115200/' /etc/mgetty/mgetty.config || true

# Locución
/opt/zeromatic/bin/make_greeting.sh

# Dependencias de sistema (idempotente)
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq sox espeak-ng flac ffmpeg python3-venv python3-pip >/dev/null || true

# Python deps (venv ligero)
if [[ ! -d /opt/zeromatic/venv ]]; then
  python3 -m venv /opt/zeromatic/venv
fi
/opt/zeromatic/venv/bin/pip install -q --upgrade pip
if [[ -f "$SRC/requirements.txt" ]]; then
  /opt/zeromatic/venv/bin/pip install -q -r "$SRC/requirements.txt"
else
  /opt/zeromatic/venv/bin/pip install -q pyserial SpeechRecognition openai
fi

install -d -m 755 /opt/zeromatic/media

# Apuntar scripts a venv
for py in zeromatic_daemon.py transcribe.py send_mail.py test_call.py \
          diag_line.py diag_quick.py hw_modem_test.py hw_voice_probe.py test_ring.py; do
  if [[ -f /opt/zeromatic/bin/$py ]]; then
    sed -i '1s|.*|#!/opt/zeromatic/venv/bin/python3|' "/opt/zeromatic/bin/$py"
  fi
done

# Desactivar getty serial si existe
systemctl stop serial-getty@ttyACM0.service 2>/dev/null || true
systemctl disable serial-getty@ttyACM0.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable zeromatic.service
systemctl restart zeromatic.service
sleep 2
systemctl --no-pager -l status zeromatic.service || true
echo "Instalación lista. Configura /etc/zeromatic/email.env y prueba con /opt/zeromatic/bin/test_pipeline.sh"
