# Instalación en Raspberry Pi

## Requisitos

- Raspberry Pi OS (Bookworm u similar) con red
- Módem USB Conexant CX93010 conectado
- Cable RJ11 a línea analógica
- Usuario con `sudo` (p. ej. `pi`)

## 1. Clonar e instalar

```bash
cd ~
git clone https://github.com/fernandoog/buzonera.git
cd buzonera
sudo apt update
sudo apt install -y python3-venv python3-pip sox espeak-ng flac ffmpeg \
  python3-serial minicom
sudo bash bin/install_on_pi.sh "$PWD"
```

`install_on_pi.sh` copia binarios a `/opt/buzonera`, crea el venv, instala dependencias Python, configura spool y habilita systemd.

## 2. Correo

```bash
sudo cp /etc/buzonera/email.env /etc/buzonera/email.env.bak 2>/dev/null || true
sudo nano /etc/buzonera/email.env
```

Necesitas una [contraseña de aplicación de Gmail](https://myaccount.google.com/apppasswords) (cuenta con 2FA).

## 3. Locución y servicio

```bash
sudo /opt/buzonera/bin/make_greeting_wav.sh
sudo systemctl enable --now buzonera
sudo systemctl status buzonera
sudo journalctl -u buzonera -f
```

Para una voz más natural, genera `greeting.wav` en un PC (`bin/make_greeting_kokoro.py` o `bin/make_greeting_edge.ps1`) y cópialo a `/opt/buzonera/media/`.

## 4. Pruebas

```bash
# Pipeline sin teléfono
sudo /opt/buzonera/bin/test_pipeline.sh

# Test completo (opcional: número saliente)
sudo TEST_PHONE=6XXXXXXXX /opt/buzonera/bin/full_test.sh

# Hardware del módem
sudo systemctl stop buzonera
sudo /opt/buzonera/venv/bin/python3 /opt/buzonera/bin/hw_modem_test.py
sudo systemctl start buzonera
```

## Despliegue desde otro PC (opcional)

Desde Windows (PowerShell), con SSH configurado:

```powershell
.\scripts\deploy_from_pc.ps1 -HostName 192.168.1.23 -User pi
```

Ajusta host, usuario y clave SSH según tu entorno.
