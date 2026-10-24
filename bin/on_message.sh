#!/bin/bash
# Llamado por vgetty tras grabar un mensaje. Arg1 = ruta del .rmd
set -euo pipefail

SRC="${1:-}"
LOG=/var/log/zeromatic.log
WORKDIR=/var/spool/voice/processed
ENV_FILE=/etc/zeromatic/email.env
mkdir -p "$WORKDIR"
exec >>"$LOG" 2>&1

# Cargar LINE_NUMBER / CALLED_ID desde email.env si no vienen en el entorno
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # Evitar que valores con espacios rompan el source
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    case "$key" in
      LINE_NUMBER|CALLED_ID|MAIL_TO) printf -v "$key" '%s' "$val" ;;
    esac
  done <"$ENV_FILE"
  set +a
fi

# Called-ID = número de esta línea (DNIS). En analógico se configura a mano.
if [[ -z "${CALLED_ID:-}" && -n "${LINE_NUMBER:-}" ]]; then
  CALLED_ID="$LINE_NUMBER"
fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "$(ts) === nuevo mensaje: ${SRC:-<vacio>} CALLER_ID=${CALLER_ID:-?} CALLED_ID=${CALLED_ID:-?} DEVICE=${DEVICE:-?} ==="

if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "$(ts) ERROR: fichero de audio ausente"
  exit 1
fi

BASE="$(basename "$SRC")"
BASE="${BASE%.*}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTDIR="$WORKDIR/${STAMP}_${BASE}"
mkdir -p "$OUTDIR"
WAV="$OUTDIR/mensaje.wav"
MP3="$OUTDIR/mensaje.mp3"
TXT="$OUTDIR/transcripcion.txt"
META="$OUTDIR/meta.txt"

{
  echo "Fecha: $(ts)"
  echo "Caller-ID: ${CALLER_ID:-desconocido}"
  echo "Called-ID: ${CALLED_ID:-no configurado}"
  echo "Dispositivo: ${DEVICE:-}"
  echo "Origen: $SRC"
} >"$META"

case "${SRC,,}" in
  *.wav)
    cp "$SRC" "$WAV"
    ;;
  *.rmd)
    if ! rmdtopvf "$SRC" | pvftowav >"$WAV" 2>/tmp/zeromatic_conv.err; then
      echo "$(ts) WARN rmdtopvf falló"
      cp "$SRC" "$OUTDIR/mensaje.rmd"
    fi
    ;;
  *)
    ffmpeg -y -i "$SRC" -ar 8000 -ac 1 "$WAV" >/dev/null 2>&1 || cp "$SRC" "$WAV"
    ;;
esac

if [[ -f "$WAV" && -s "$WAV" ]]; then
  ffmpeg -y -i "$WAV" -codec:a libmp3lame -qscale:a 4 "$MP3" >/dev/null 2>&1 || \
    sox "$WAV" "$MP3" 2>/dev/null || true
else
  echo "$(ts) ERROR: no se pudo generar WAV"
  WAV=""
  MP3=""
fi

# Transcripción
export CONTESTADOR_WAV="$WAV"
export CONTESTADOR_TXT="$TXT"
/opt/zeromatic/bin/transcribe.py || echo "(sin transcripción)" >"$TXT"

# Envío de correo
/opt/zeromatic/bin/send_mail.py \
  --meta "$META" \
  --transcript "$TXT" \
  --audio "${MP3:-$WAV}" \
  || echo "$(ts) ERROR enviando correo"

# Conservar original
cp -n "$SRC" "$OUTDIR/" 2>/dev/null || true
echo "$(ts) OK procesado en $OUTDIR"
