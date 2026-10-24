# Arquitectura

## Visión general

```
Línea PSTN (RJ11)
       │
  Módem USB Conexant CX93010  ──USB──►  /dev/ttyACM0 (cdc_acm)
       │
  contestador_daemon.py  (systemd: contestador.service)
       │
       ├─ RING → AT+FCLASS=8 / AT+VLS=1 → contestar
       ├─ Locución greeting.wav via AT+VTX
       ├─ Grabación AT+VRX → WAV 8 kHz PCM
       └─ on_message.sh
              ├─ sox → MP3
              ├─ transcribe.py (Whisper o Google STT)
              └─ send_mail.py → Gmail SMTP
```

No usa Asterisk ni `chan_dongle`. Es un contestador **analógico** (PSTN) con comandos AT V.253.

## Componentes

| Pieza | Rol |
|-------|-----|
| `bin/contestador_daemon.py` | Escucha RING, contesta, reproduce, graba |
| `bin/on_message.sh` | Post-proceso de cada mensaje |
| `bin/transcribe.py` | STT (OpenAI Whisper o Google Web Speech) |
| `bin/send_mail.py` | SMTP Gmail con adjunto |
| `systemd/contestador.service` | Arranque y reinicio |
| `/etc/contestador/email.env` | Secretos (no versionado) |

## Flujo de una llamada entrante

1. El módem emite `RING` (opcionalmente líneas `DATE`/`NMBR`/`NAME` si Caller ID está activo).
2. Tras `RINGS_TO_ANSWER` timbres, el daemon entra en modo voz y descolga.
3. Reproduce `greeting.wav` (+ beep) a ritmo tiempo real por `AT+VTX`.
4. Graba con `AT+VRX` hasta silencio (`SILENCE_HANG_SEC`) o `MAX_RECORD_SEC`.
5. Guarda WAV en `/var/spool/voice/incoming/` y lanza `on_message.sh`.
6. El pipeline deja el resultado en `/var/spool/voice/processed/<timestamp>/`.

## Dependencias del sistema (Pi)

- `python3` + venv
- `pyserial`
- `sox`, `espeak-ng` (locución)
- `flac` (recomendado para Google STT)
- `ffmpeg` u opcional vía sox para MP3
- Grupo `dialout` / acceso a `/dev/ttyACM0`

## Limitaciones conocidas

- El Conexant a veces rechaza `ATA` en modo data; el daemon usa secuencia voz (`FCLASS=8` + `VLS`).
- Caller ID depende de la operadora y del cableado; el Called-ID se configura a mano (`LINE_NUMBER`).
- Un solo proceso puede abrir el módem: parar `contestador` antes de diagnósticos manuales.
