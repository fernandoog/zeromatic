# Genera greeting.wav con Microsoft Edge Neural TTS (voz natural).
# Requiere: edge-tts (>=7.3.1), ffmpeg
param(
    [string]$Voice = "es-ES-ElviraNeural",
    [string]$Text = "Hola. Deja tu mensaje después de la señal. Gracias.",
    [string]$OutDir = (Join-Path $PSScriptRoot "..\media")
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$raw = Join-Path $OutDir "greeting_edge_raw.mp3"
$out = Join-Path $OutDir "greeting.wav"

edge-tts --voice $Voice --rate="-5%" --text $Text --write-media $raw
if ((Get-Item $raw).Length -lt 1000) { throw "edge-tts no generó audio" }

ffmpeg -y -i $raw -ar 8000 -ac 1 -acodec pcm_s16le -af "volume=3dB" $out
Write-Host "OK $out ($Voice)"
