# Arquitectura

## Visión general

```
Línea PSTN (RJ11)
       │
  Módem USB Conexant CX93010  ──USB──►  /dev/ttyACM0 (cdc_acm)
       │
  zeromatic_daemon.py  (systemd: zeromatic.service)
       │
       ├─ RING → AT+FCLASS=8 / AT+VLS=1 → contestar
       ├─ Locución greeting.wav via AT+VTX
       ├─ Grabación AT+VRX → WAV 8 kHz PCM
       ├─ Colgado → force_recover (vuelta a data mode)
       └─ on_message.sh (async)
              ├─ sox → MP3
              ├─ transcribe.py (Whisper o Google STT)
              └─ send_mail.py → Gmail SMTP
```

Contestador **analógico** (PSTN) con comandos AT V.253. Sin centralita PBX.

## Componentes

| Pieza | Rol |
|-------|-----|
| `bin/zeromatic_daemon.py` | Escucha RING, contesta, reproduce, graba, recupera módem |
| `bin/on_message.sh` | Post-proceso de cada mensaje |
| `bin/transcribe.py` | STT (OpenAI Whisper o Google Web Speech) |
| `bin/send_mail.py` | SMTP Gmail con adjunto |
| `systemd/zeromatic.service` | Arranque y reinicio |
| `/etc/zeromatic/email.env` | Secretos (no versionado) |

## Flujo de una llamada entrante

1. El módem emite `RING` (opcionalmente líneas `DATE`/`NMBR`/`NAME` si Caller ID está activo).
2. Tras `RINGS_TO_ANSWER` timbres, el daemon entra en modo voz y descolga.
3. Reproduce `greeting.wav` (+ beep) a ritmo tiempo real por `AT+VTX`.
4. Graba con `AT+VRX` hasta silencio (`SILENCE_HANG_SEC`), DLE de colgado o `MAX_RECORD_SEC`.
5. Guarda WAV en `/var/spool/voice/incoming/` y lanza `on_message.sh` en segundo plano.
6. `force_recover()` saca al módem del stream PCM y vuelve a modo datos para el siguiente RING.
7. El pipeline deja el resultado en `/var/spool/voice/processed/<timestamp>/`.

## Dependencias del sistema (Pi)

- `python3` + venv
- `pyserial`
- `sox`, `espeak-ng` (locución de respaldo)
- `flac` (recomendado para Google STT)
- `ffmpeg` u opcional vía sox para MP3
- Grupo `dialout` / acceso a `/dev/ttyACM0`

## Limitaciones conocidas

- El Conexant a veces rechaza `ATA` en modo data; el daemon usa secuencia voz (`FCLASS=8` + `VLS`).
- Tras VRX el chip puede quedarse tirando PCM: hace falta reopen/reset USB.
- Caller ID depende de la operadora y del cableado; el Called-ID se configura a mano (`LINE_NUMBER`).
- Un solo proceso puede abrir el módem: parar `zeromatic` antes de diagnósticos manuales.
