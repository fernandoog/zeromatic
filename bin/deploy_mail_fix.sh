#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/on_message.sh /tmp/send_mail.py /tmp/buzonera_daemon.py /tmp/README.md 2>/dev/null || true
install -m 755 /tmp/on_message.sh /opt/buzonera/bin/on_message.sh
install -m 755 /tmp/send_mail.py /opt/buzonera/bin/send_mail.py
install -m 755 /tmp/buzonera_daemon.py /opt/buzonera/bin/buzonera_daemon.py
install -m 644 /tmp/README.md /opt/buzonera/README.md
python3 - <<'PY'
from pathlib import Path
for name in ("send_mail.py", "buzonera_daemon.py"):
    p = Path("/opt/buzonera/bin") / name
    lines = p.read_text().splitlines(True)
    lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
    p.write_text("".join(lines))
PY
# Asegurar clave LINE_NUMBER en email.env (sin pisar valor si ya existe)
if ! grep -q '^LINE_NUMBER=' /etc/buzonera/email.env 2>/dev/null; then
  printf '\n# Numero de esta linea (Called-ID en el correo)\nLINE_NUMBER=\n' >> /etc/buzonera/email.env
fi
chmod 600 /etc/buzonera/email.env
systemctl restart buzonera
sleep 2
systemctl is-active buzonera
echo "LINE_NUMBER actual:"
grep '^LINE_NUMBER=' /etc/buzonera/email.env || true
