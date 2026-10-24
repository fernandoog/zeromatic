#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/on_message.sh /tmp/send_mail.py /tmp/contestador_daemon.py /tmp/README.md 2>/dev/null || true
install -m 755 /tmp/on_message.sh /opt/contestador/bin/on_message.sh
install -m 755 /tmp/send_mail.py /opt/contestador/bin/send_mail.py
install -m 755 /tmp/contestador_daemon.py /opt/contestador/bin/contestador_daemon.py
install -m 644 /tmp/README.md /opt/contestador/README.md
python3 - <<'PY'
from pathlib import Path
for name in ("send_mail.py", "contestador_daemon.py"):
    p = Path("/opt/contestador/bin") / name
    lines = p.read_text().splitlines(True)
    lines[0] = "#!/opt/contestador/venv/bin/python3\n"
    p.write_text("".join(lines))
PY
# Asegurar clave LINE_NUMBER en email.env (sin pisar valor si ya existe)
if ! grep -q '^LINE_NUMBER=' /etc/contestador/email.env 2>/dev/null; then
  printf '\n# Numero de esta linea (Called-ID en el correo)\nLINE_NUMBER=\n' >> /etc/contestador/email.env
fi
chmod 600 /etc/contestador/email.env
systemctl restart contestador
sleep 2
systemctl is-active contestador
echo "LINE_NUMBER actual:"
grep '^LINE_NUMBER=' /etc/contestador/email.env || true
