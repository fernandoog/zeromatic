#!/bin/bash
# Genera la locución en formato RMD para el módem Conexant (V.253)
set -euo pipefail

MSG_DIR=/var/spool/voice/messages
TMP=/tmp/zeromatic_greeting
mkdir -p "$MSG_DIR" "$TMP"

TEXT="${1:-Deje su mensaje después de la señal.}"

# WAV 8 kHz mono (estándar telefónico)
espeak-ng -v es -s 140 -w "$TMP/greeting.wav" "$TEXT"
sox "$TMP/greeting.wav" -r 8000 -c 1 -b 16 "$TMP/greeting8k.wav"

# WAV -> PVF -> RMD (V253 unsigned PCM 8-bit = modo 8)
wavtopvf "$TMP/greeting8k.wav" "$TMP/greeting.pvf"
pvftormd V253modem 8 "$TMP/greeting.pvf" "$MSG_DIR/standard.rmd"

echo "standard.rmd" >"$MSG_DIR/Index"
chown -R root:voice "$MSG_DIR"
chmod 664 "$MSG_DIR/standard.rmd" "$MSG_DIR/Index"

echo "Locución lista: $MSG_DIR/standard.rmd"
pvffile "$TMP/greeting.pvf" || true
rmdfile "$MSG_DIR/standard.rmd" || true
