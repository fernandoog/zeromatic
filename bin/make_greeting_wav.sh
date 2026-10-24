#!/bin/bash
# Locución para el módem. Preferir greeting.wav ya generado (p.ej. Edge Neural TTS).
# Solo usa espeak-ng si falta el fichero.
set -euo pipefail
OUT=/opt/buzonera/media/greeting.wav
mkdir -p /opt/buzonera/media

if [[ -f "$OUT" && -s "$OUT" && "${FORCE_ESPEAK:-0}" != "1" ]]; then
  echo "OK $OUT (ya existía; FORCE_ESPEAK=1 para regenerar con espeak)"
  exit 0
fi

TMP=/tmp/greeting_espeak.wav
espeak-ng -v es -s 130 -a 200 -w "$TMP" "Hola. Deja tu mensaje después de la señal. Gracias."
sox "$TMP" -r 8000 -c 1 -b 16 "$OUT" vol 4 dB
echo "OK $OUT (espeak-ng fallback)"
