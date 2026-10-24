#!/bin/bash
# Test completo del Buzonera
set -uo pipefail
PASS=0
FAIL=0
WARN=0

ok()   { echo "[OK]   $*"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "[WARN] $*"; WARN=$((WARN+1)); }
sec()  { echo; echo "======== $* ========"; }

sec "1. Host / módem / servicio"
hostname; date; uptime
if systemctl is-active --quiet buzonera; then
  ok "buzonera.service active"
else
  bad "buzonera.service no activo: $(systemctl is-active buzonera)"
fi
if [[ -c /dev/ttyACM0 ]]; then
  ok "módem /dev/ttyACM0 presente"
else
  bad "módem /dev/ttyACM0 ausente"
fi
if lsusb 2>/dev/null | grep -qi conexant; then
  ok "USB Conexant detectado"
else
  warn "USB Conexant no listado en lsusb"
fi
if [[ -f /opt/buzonera/media/greeting.wav ]]; then
  ok "greeting.wav presente ($(stat -c%s /opt/buzonera/media/greeting.wav) bytes)"
else
  bad "falta /opt/buzonera/media/greeting.wav"
fi
if [[ -x /opt/buzonera/bin/buzonera_daemon.py ]]; then
  ok "daemon instalado"
else
  bad "daemon no ejecutable"
fi

sec "2. Config correo"
ENV=/etc/buzonera/email.env
if [[ -f "$ENV" ]]; then
  ok "email.env presente"
  # shellcheck disable=SC1090
  set -a; source "$ENV"; set +a
  [[ -n "${MAIL_TO:-}" ]] && ok "MAIL_TO=$MAIL_TO" || bad "MAIL_TO vacío"
  [[ -n "${GMAIL_USER:-}" ]] && ok "GMAIL_USER=$GMAIL_USER" || bad "GMAIL_USER vacío"
  if [[ -n "${GMAIL_APP_PASSWORD:-}" && "$GMAIL_APP_PASSWORD" != "xxxx xxxx xxxx xxxx" && ${#GMAIL_APP_PASSWORD} -ge 8 ]]; then
    ok "GMAIL_APP_PASSWORD configurada (${#GMAIL_APP_PASSWORD} chars)"
  else
    bad "GMAIL_APP_PASSWORD ausente o placeholder"
  fi
else
  bad "falta $ENV"
fi

sec "3. Línea telefónica (tono de marcado)"
systemctl stop buzonera.service
sleep 2
LINE_OUT=$(/opt/buzonera/venv/bin/python3 - <<'PY'
import serial, time
ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.3, rtscts=True)
time.sleep(0.4)

def cmd(c, w=12):
    ser.reset_input_buffer()
    ser.write((c + "\r").encode()); ser.flush()
    end = time.time() + w
    buf = b""
    while time.time() < end:
        ch = ser.read(256)
        if ch:
            buf += ch
            u = buf.upper()
            if any(x in u for x in (b"OK", b"ERROR", b"NO DIAL", b"NO CARRIER", b"BUSY", b"CONNECT", b"VCON")):
                time.sleep(0.15)
                buf += ser.read(ser.in_waiting or 0)
                break
        else:
            time.sleep(0.02)
    t = buf.decode("ascii", errors="ignore")
    print(f">>> {c} | <<< {t!r}".replace("\r", "\\r").replace("\n", "\\n"))
    return t.upper()

cmd("ATH"); cmd("ATZ", 4); cmd("ATE0"); cmd("ATQ0V1"); cmd("ATX4"); cmd("ATS7=8")
print("--- tono ---")
r = cmd("ATDTW;", 15)
cmd("ATH")
print("--- digito ---")
r2 = cmd("ATDT1;", 12)
cmd("ATH")
joined = r + " " + r2
if "NO DIAL" in joined:
    print("LINE_VERDICT=NO_DIALTONE")
elif any(x in joined for x in ("OK", "VCON", "NO CARRIER", "BUSY")):
    print("LINE_VERDICT=OK")
else:
    print("LINE_VERDICT=UNCLEAR")
ser.close()
PY
)
echo "$LINE_OUT"
if echo "$LINE_OUT" | grep -q "LINE_VERDICT=OK"; then
  ok "línea con tono / respuesta AT válida"
elif echo "$LINE_OUT" | grep -q "LINE_VERDICT=NO_DIALTONE"; then
  bad "NO DIALTONE — cable de línea o RJ11"
else
  warn "resultado de línea poco claro"
fi

sec "4. Pipeline audio → transcripción → correo (sin llamada)"
systemctl start buzonera.service 2>/dev/null || true
# El pipeline no necesita el módem; paramos buzonera solo si on_message no lo necesita
LOG_BEFORE=$(wc -l < /var/log/buzonera.log 2>/dev/null || echo 0)
if /opt/buzonera/bin/test_pipeline.sh; then
  ok "test_pipeline.sh terminó OK"
else
  bad "test_pipeline.sh falló (exit $?)"
fi
sleep 1
if [[ -f /var/log/buzonera.log ]]; then
  echo "--- buzonera.log (nuevas líneas) ---"
  tail -n +$((LOG_BEFORE + 1)) /var/log/buzonera.log 2>/dev/null | tail -40
  if tail -n +$((LOG_BEFORE + 1)) /var/log/buzonera.log 2>/dev/null | grep -qiE "enviado|mail sent|SMTP|Message sent|OK.*mail|correo enviado"; then
    ok "log indica envío de correo"
  elif tail -n +$((LOG_BEFORE + 1)) /var/log/buzonera.log 2>/dev/null | grep -qiE "error|fail|smtp|auth|535|534"; then
    bad "error en envío de correo (ver log)"
  else
    warn "no se ve confirmación clara de correo en log"
  fi
  LATEST=$(ls -1dt /var/spool/voice/processed/*/ 2>/dev/null | head -1 || true)
  if [[ -n "$LATEST" ]]; then
    ok "mensaje procesado en $LATEST"
    ls -la "$LATEST"
  else
    warn "no hay carpeta en processed/"
  fi
else
  warn "no existe /var/log/buzonera.log"
fi

sec "5. Llamada saliente con locución"
TEST_PHONE="${TEST_PHONE:-}"
if [[ -z "$TEST_PHONE" ]]; then
  warn "TEST_PHONE no definido — se omite llamada saliente (export TEST_PHONE=6XXXXXXXX)"
else
  echo "Destino: $TEST_PHONE (contesta el móvil en ~25s si suena)"
  systemctl stop buzonera.service
  sleep 2
  CALL_OUT=$(/opt/buzonera/venv/bin/python3 /opt/buzonera/bin/test_call.py "$TEST_PHONE" 2>&1) || true
  echo "$CALL_OUT"
  if echo "$CALL_OUT" | grep -qi "NO DIALTONE"; then
    bad "llamada saliente: NO DIALTONE"
  elif echo "$CALL_OUT" | grep -qiE "ERROR|Traceback|falló|failed"; then
    bad "llamada saliente con errores"
  elif echo "$CALL_OUT" | grep -qiE "VCON|CONNECT|reproduc|greeting|enviado|OK"; then
    ok "llamada saliente completó secuencia AT/voz"
  else
    warn "llamada saliente: revisar salida manualmente"
  fi
fi

sec "6. Restaurar servicio"
systemctl start buzonera.service
sleep 3
if systemctl is-active --quiet buzonera; then
  ok "buzonera reiniciado y activo"
  journalctl -u buzonera -n 8 --no-pager
else
  bad "no se pudo reiniciar buzonera"
fi

sec "RESUMEN"
echo "OK=$PASS  FAIL=$FAIL  WARN=$WARN"
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
