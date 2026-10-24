# Changelog

## 1.2.0 — 2026-10-24

- Nombre del proyecto: **Zeromatic** (Pi **Zero** + **automatic** answering machine).
- Grabación inmediata tras el beep; sin recortar el inicio del mensaje.
- Daemon compatible con instalaciones legadas (`/opt/contestador`, `/opt/buzonera`).

## 1.1.0 — 2026-10-24

- Detección de colgado más rápida y recorte de cola del WAV.
- Recover agresivo del módem tras cada llamada.
- Reproducción VTX con ritmo por reloj absoluto (Pi Zero).
- Locución opcional Kokoro-82M / Edge Neural TTS.
- Pipeline de correo asíncrono.

## 1.0.0 — 2026-10-24

- Daemon Python para módem USB Conexant CX93010.
- Pipeline: WAV → MP3 → transcripción → correo Gmail.
- Scripts de instalación, diagnóstico y test end-to-end.
- Informe de test de hardware (2026-10-24).
