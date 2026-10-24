#!/bin/bash
# Prueba sin llamada: genera WAV de prueba y ejecuta pipeline de correo/transcripción
set -euo pipefail
TMP=/tmp/zeromatic_test
mkdir -p "$TMP" /var/spool/voice/incoming
espeak-ng -v es -s 140 -w "$TMP/sample.wav" \
  "Hola Fernando, esto es un mensaje de prueba del contestador automático."
sox "$TMP/sample.wav" -r 8000 -c 1 -b 16 "$TMP/sample8k.wav"
DST=/var/spool/voice/incoming/test_$(date +%s).wav
cp "$TMP/sample8k.wav" "$DST"
export CALLER_ID="PRUEBA"
export DEVICE="ttyACM0"
/opt/zeromatic/bin/on_message.sh "$DST"
echo "Revisa /var/log/zeromatic.log y tu correo."
