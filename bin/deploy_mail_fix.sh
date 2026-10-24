#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/on_message.sh /tmp/send_mail.py /tmp/zeromatic_daemon.py /tmp/README.md 2>/dev/null || true
install -m 755 /tmp/on_message.sh /opt/zeromatic/bin/on_message.sh
install -m 755 /tmp/send_mail.py /opt/zeromatic/bin/send_mail.py
install -m 755 /tmp/zeromatic_daemon.py /opt/zeromatic/bin/zeromatic_daemon.py
install -m 644 /tmp/README.md /opt/zeromatic/README.md
python3 - <<'PY'
from pathlib import Path
for name in ("send_mail.py", "zeromatic_daemon.py"):
    p = Path("/opt/zeromatic/bin") / name
    lines = p.read_text().splitlines(True)
    lines[0] = "#!/opt/zeromatic/venv/bin/python3\n"
    p.write_text("".join(lines))
PY
# Asegurar clave LINE_NUMBER en email.env (sin pisar valor si ya existe)
if ! grep -q '^LINE_NUMBER=' /etc/zeromatic/email.env 2>/dev/null; then
  printf '\n# Numero de esta linea (Called-ID en el correo)\nLINE_NUMBER=\n' >> /etc/zeromatic/email.env
fi
chmod 600 /etc/zeromatic/email.env
systemctl restart zeromatic
sleep 2
systemctl is-active zeromatic
echo "LINE_NUMBER actual:"
grep '^LINE_NUMBER=' /etc/zeromatic/email.env || true
