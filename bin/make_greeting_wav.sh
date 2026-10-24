#!/bin/bash
set -euo pipefail
OUT=/opt/contestador/media/greeting.wav
mkdir -p /opt/contestador/media
TMP=/tmp/greeting_espeak.wav
espeak-ng -v es -s 130 -a 200 -w "$TMP" "Hola. Deja tu mensaje después de la señal. Gracias."
# 8 kHz mono + un poco más de volumen para la línea analógica
sox "$TMP" -r 8000 -c 1 -b 16 "$OUT" vol 4 dB
echo "OK $OUT"
