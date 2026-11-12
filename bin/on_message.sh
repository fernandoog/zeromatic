#!/bin/bash
# Pipeline post-grabación: transcribe + mail. Arg1 = ruta del .wav/.rmd
# Tras correo OK se borran todos los temporales; el rastro queda en el log.
set -euo pipefail

SRC="${1:-}"
LOG="${ZEROMATIC_LOG:-/var/log/zeromatic.log}"
WORKDIR=/var/spool/voice/processed
ENV_FILE=/etc/zeromatic/email.env
mkdir -p "$WORKDIR" "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

# Cargar LINE_NUMBER / CALLED_ID desde email.env si no vienen en el entorno
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
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

cleanup_all() {
  rm -f -- "$SRC" 2>/dev/null || true
  rm -rf -- "$OUTDIR" 2>/dev/null || true
  rm -f /tmp/zeromatic_conv.err /tmp/zeromatic_gsm_*.wav /tmp/zeromatic_gsm_*.amr \
        /tmp/zeromatic_beep.wav /tmp/zeromatic_amr_src.wav /tmp/zeromatic_amr_out.amr \
        2>/dev/null || true
}

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

export CONTESTADOR_WAV="$WAV"
export CONTESTADOR_TXT="$TXT"
/opt/zeromatic/bin/transcribe.py || echo "(sin transcripción)" >"$TXT"

AUDIO_ATTACH="${MP3:-}"
if [[ -z "$AUDIO_ATTACH" || ! -s "$AUDIO_ATTACH" ]]; then
  AUDIO_ATTACH="${WAV:-}"
fi

if /opt/zeromatic/bin/send_mail.py \
  --meta "$META" \
  --transcript "$TXT" \
  --audio "${AUDIO_ATTACH}"; then
  echo "$(ts) Correo enviado OK — eliminando temporales (src=$SRC outdir=$OUTDIR)"
  cleanup_all
  echo "$(ts) Limpieza completada (solo queda el log en $LOG)"
  exit 0
fi

echo "$(ts) ERROR enviando correo — se conservan $OUTDIR y $SRC para depurar"
exit 1
