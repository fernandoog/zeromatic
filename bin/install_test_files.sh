#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/full_test.sh /tmp/test_pipeline.sh /tmp/test_call.py /tmp/on_message.sh /tmp/install_test_files.sh 2>/dev/null || true
install -m 755 /tmp/full_test.sh /opt/buzonera/bin/
install -m 755 /tmp/test_pipeline.sh /opt/buzonera/bin/
install -m 755 /tmp/test_call.py /opt/buzonera/bin/
install -m 755 /tmp/on_message.sh /opt/buzonera/bin/
python3 - <<'PY'
from pathlib import Path
p = Path("/opt/buzonera/bin/test_call.py")
lines = p.read_text().splitlines(True)
lines[0] = "#!/opt/buzonera/venv/bin/python3\n"
p.write_text("".join(lines))
PY
echo READY
ls -la /opt/buzonera/bin/full_test.sh /opt/buzonera/bin/test_call.py /opt/buzonera/bin/on_message.sh /opt/buzonera/bin/test_pipeline.sh
