# Zeromatic

**Zero** (Raspberry Pi Zero) + **automatic** answering machine.

Repo: [github.com/fernandoog/contestador](https://github.com/fernandoog/contestador)

Contestador telefónico automático para **Raspberry Pi Zero** + **módem USB Conexant CX93010**.

Daemon Python ligero: contesta la línea analógica, reproduce una locución, graba el mensaje, lo transcribe y lo envía por correo.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Características

- Contesta al N-ésimo timbre (`RINGS_TO_ANSWER`)
- Locución + beep + grabación (V.253: `AT+VTX` / `AT+VRX`)
- Detección rápida de colgado (silencio / tono / DLE) y recorte de cola
- Grabación al instante tras el beep (sin cortar el inicio del mensaje)
- Recuperación del módem tras cada llamada (evita quedar sordo al siguiente RING)
- Transcripción en español (OpenAI Whisper o Google Web Speech)
- Correo con audio (MP3/WAV) + texto vía Gmail SMTP
- Locución opcional con **Kokoro** / Edge Neural TTS (generada en el PC)
- Unidad `systemd` con reinicio automático

## Hardware

| Pieza | Recomendado |
|-------|-------------|
| SBC | Raspberry Pi Zero W / 3 / 4 |
| Módem | USB **hardware** Conexant CX93010 (`0572:1340`) |
| Línea | PSTN analógica (RJ11) |

Detalle y resultado del test de hardware: [docs/HARDWARE.md](docs/HARDWARE.md) · [informe 2026-10-24](INFORME-hardware-modem-2026-10-24.md).

## Instalación rápida

```bash
git clone https://github.com/fernandoog/contestador.git
cd contestador
sudo apt install -y python3-venv sox espeak-ng flac ffmpeg
sudo bash bin/install_on_pi.sh "$PWD"
sudo nano /etc/zeromatic/email.env   # App Password de Gmail
sudo systemctl enable --now zeromatic
```

Guía completa: [docs/INSTALL.md](docs/INSTALL.md).

## Uso diario

Con el servicio activo no hay que hacer nada:

1. Llamada entrante → contesta
2. Locución de bienvenida + beep
3. Graba hasta silencio o `MAX_RECORD_SEC`
4. Transcribe y envía correo (en segundo plano)

```bash
sudo systemctl status zeromatic
sudo journalctl -u zeromatic -f
```

**Importante:** si usas el módem a mano (`test_call.py`, `hw_modem_test.py`, `minicom`), para el servicio antes; si no, pelean por `/dev/ttyACM0`.

## Configuración de correo

1. Crea una [contraseña de aplicación](https://myaccount.google.com/apppasswords) (Gmail + 2FA).
2. Edita `/etc/zeromatic/email.env` (plantilla: `etc/email.env.example`).

```bash
MAIL_TO=tu@email.com
MAIL_FROM=tu@email.com
GMAIL_USER=tu@email.com
GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
LINE_NUMBER=912345678
# Opcional:
# OPENAI_API_KEY=sk-...
```

Prueba sin llamada real:

```bash
sudo /opt/zeromatic/bin/test_pipeline.sh
tail -50 /var/log/zeromatic.log
```

## Locución (voz)

En la Pi, por defecto `espeak-ng` si no hay WAV. Para una voz más natural, genera en el PC:

```powershell
# Kokoro-82M (recomendado) — requiere models/kokoro/*.onnx
python bin/make_greeting_kokoro.py --voice ef_dora

# O Edge Neural TTS
.\bin\make_greeting_edge.ps1
```

Copia `media/greeting.wav` a `/opt/zeromatic/media/greeting.wav` en la Pi.

## Pruebas

| Script | Qué hace |
|--------|----------|
| `bin/full_test.sh` | Servicio + línea + pipeline + llamada saliente opcional |
| `bin/test_pipeline.sh` | Audio → MP3 → correo (sin teléfono) |
| `bin/test_call.py <número>` | Llamada saliente con locución |
| `bin/hw_modem_test.py` | Test de hardware / AT / tono |
| `bin/hw_voice_probe.py` | Capacidades de voz (VSM/VLS/VRX) |
| `bin/diag_line.py` | Diagnóstico de línea |

```bash
sudo TEST_PHONE=6XXXXXXXX /opt/zeromatic/bin/full_test.sh
sudo systemctl stop zeromatic
sudo /opt/zeromatic/venv/bin/python3 /opt/zeromatic/bin/test_call.py 6XXXXXXXX
sudo systemctl start zeromatic
```

## Rutas en la Pi

| Qué | Ruta |
|-----|------|
| Binarios | `/opt/zeromatic/bin/` |
| Locución | `/opt/zeromatic/media/greeting.wav` |
| Config correo | `/etc/zeromatic/email.env` |
| Entrantes | `/var/spool/voice/incoming/` |
| Procesados | `/var/spool/voice/processed/` |
| Log pipeline | `/var/log/zeromatic.log` |
| Log daemon | `journalctl -u zeromatic` |
| systemd | `/etc/systemd/system/zeromatic.service` |

## Variables del servicio

En `systemd/zeromatic.service` (o override):

| Variable | Default | Significado |
|----------|---------|-------------|
| `RINGS_TO_ANSWER` | `1` | Timbres antes de contestar |
| `MAX_RECORD_SEC` | `90` | Máximo de grabación |
| `SILENCE_HANG_SEC` | `2.0` | Silencio para cortar |
| `MODEM_DEVICE` | `/dev/ttyACM0` | Puerto del módem |
| `MODEM_BAUD` | `115200` | Velocidad serie |

```bash
sudo systemctl daemon-reload
sudo systemctl restart zeromatic
```

## Estructura del repositorio

```
zeromatic/
├── bin/                 # daemon, pipeline, tests, diagnóstico, TTS
├── docs/                # arquitectura, instalación, hardware
├── etc/                 # email.env.example, voice.conf
├── media/               # greeting.wav (generado)
├── scripts/             # despliegue desde PC
├── systemd/             # zeromatic.service
├── INFORME-hardware-modem-2026-10-24.md
├── requirements.txt
└── README.md
```

Arquitectura: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Problemas frecuentes

| Síntoma | Qué mirar |
|---------|-----------|
| No contesta | `systemctl status zeromatic` · ¿otro proceso en el módem? |
| Segunda llamada no contesta | Recover post-llamada; `journalctl -u zeromatic` |
| Se corta el inicio del mensaje | Beep → VRX inmediato (ya corregido) |
| `NO DIALTONE` | Cable RJ11 / filtro ADSL / centralita |
| Correo no llega | `GMAIL_APP_PASSWORD` · `/var/log/zeromatic.log` |
| Sin transcripción | `sudo apt install flac` o `OPENAI_API_KEY` |
| Tras reinicio no arranca | `sudo systemctl enable --now zeromatic` |

## Licencia

[MIT](LICENSE) · [Fernando Ortega](https://github.com/fernandoog)
