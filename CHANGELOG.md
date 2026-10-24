# Changelog

## 1.1.0 — 2026-10-24

- Rebrand del proyecto a **Buzonera** (antes «contestador»).
- Detección de colgado más rápida (silencio ~2 s, DLE-h/b, tono de marcación) y recorte de cola del WAV.
- Recover agresivo del módem tras cada llamada (DLE/ETX, reopen serie, reset USB) para no perder el siguiente RING.
- Reproducción VTX con ritmo por reloj absoluto (menos audio entrecortado en Pi Zero).
- Locución opcional con Kokoro-82M / Edge Neural TTS generada en el PC.
- Pipeline de correo asíncrono (no bloquea la escucha de RING).

## 1.0.0 — 2026-10-24

- Daemon Python para módem USB Conexant CX93010.
- Pipeline: WAV → MP3 → transcripción (Whisper u Google STT) → correo Gmail.
- Scripts de instalación, diagnóstico de línea/hardware y test end-to-end.
- Unidad systemd, ejemplos de configuración y documentación de hardware.
- Informe de test de hardware (2026-10-24): chip OK; no hace falta sustituir el módem.
