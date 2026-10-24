# Instalación en Raspberry Pi

## Requisitos

- Raspberry Pi OS (Bookworm u similar) con red
- Módem USB Conexant CX93010 conectado
- Cable RJ11 a línea analógica
- Usuario con `sudo` (p. ej. `pi`)

## 1. Clonar e instalar

```bash
cd ~
git clone https://github.com/fernandoog/zeromatic.git
cd zeromatic
sudo apt update
sudo apt install -y python3-venv python3-pip sox espeak-ng flac ffmpeg \
  python3-serial minicom
sudo bash bin/install_on_pi.sh "$PWD"
```

`install_on_pi.sh` copia binarios a `/opt/zeromatic`, crea el venv, instala dependencias Python, configura spool y habilita systemd.

## 2. Correo

```bash
sudo cp /etc/zeromatic/email.env /etc/zeromatic/email.env.bak 2>/dev/null || true
sudo nano /etc/zeromatic/email.env
```

Necesitas una [contraseña de aplicación de Gmail](https://myaccount.google.com/apppasswords) (cuenta con 2FA).

## 3. Locución y servicio

```bash
sudo /opt/zeromatic/bin/make_greeting_wav.sh
sudo systemctl enable --now zeromatic
sudo systemctl status zeromatic
sudo journalctl -u zeromatic -f
```

Para una voz más natural, genera `greeting.wav` en un PC (`bin/make_greeting_kokoro.py` o `bin/make_greeting_edge.ps1`) y cópialo a `/opt/zeromatic/media/`.

## 4. Pruebas

```bash
# Pipeline sin teléfono
sudo /opt/zeromatic/bin/test_pipeline.sh

# Test completo (opcional: número saliente)
sudo TEST_PHONE=6XXXXXXXX /opt/zeromatic/bin/full_test.sh

# Hardware del módem
sudo systemctl stop zeromatic
sudo /opt/zeromatic/venv/bin/python3 /opt/zeromatic/bin/hw_modem_test.py
sudo systemctl start zeromatic
```

## Despliegue desde otro PC (opcional)

Desde Windows (PowerShell), con SSH configurado:

```powershell
.\scripts\deploy_from_pc.ps1 -HostName 192.168.1.23 -User pi
```

Ajusta host, usuario y clave SSH según tu entorno.
