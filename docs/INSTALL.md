# Instalación en Raspberry Pi

## Requisitos

- Raspberry Pi OS (Bookworm u similar) con red
- Módem USB Conexant CX93010 conectado
- Cable RJ11 a línea analógica
- Usuario con `sudo` (p. ej. `asterisk` o `pi`)

## 1. Clonar e instalar

```bash
cd ~
git clone https://github.com/fernandoog/contestador.git
cd contestador
sudo apt update
sudo apt install -y python3-venv python3-pip sox espeak-ng flac ffmpeg \
  python3-serial minicom
sudo bash bin/install_on_pi.sh "$PWD"
```

`install_on_pi.sh` copia binarios a `/opt/contestador`, crea el venv, instala dependencias Python, configura spool y habilita systemd.

## 2. Correo

```bash
sudo cp /etc/contestador/email.env /etc/contestador/email.env.bak 2>/dev/null || true
sudo nano /etc/contestador/email.env
```

Necesitas una [contraseña de aplicación de Gmail](https://myaccount.google.com/apppasswords) (cuenta con 2FA).

## 3. Locución y servicio

```bash
sudo /opt/contestador/bin/make_greeting_wav.sh
sudo systemctl enable --now contestador
sudo systemctl status contestador
sudo journalctl -u contestador -f
```

## 4. Pruebas

```bash
# Pipeline sin teléfono
sudo /opt/contestador/bin/test_pipeline.sh

# Test completo (opcional: número saliente)
sudo TEST_PHONE=6XXXXXXXX /opt/contestador/bin/full_test.sh

# Hardware del módem
sudo systemctl stop contestador
sudo /opt/contestador/venv/bin/python3 /opt/contestador/bin/hw_modem_test.py
sudo systemctl start contestador
```

## Despliegue desde otro PC (opcional)

Desde Windows (PowerShell), con SSH configurado:

```powershell
.\scripts\deploy_from_pc.ps1 -HostName asterix.local -User asterisk
```

Ajusta host, usuario y clave SSH según tu entorno.
