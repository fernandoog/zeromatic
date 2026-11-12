# Hardware

## Configuración de referencia (actual)

| Pieza | Detalle |
|-------|---------|
| SBC | Raspberry Pi Zero W |
| Módem | **SIMCom SIM800C** (GSM) vía adaptador USB-UART **CH340** (`1a86:7523`) |
| Línea | Red móvil (SIM) |
| Driver Linux | `ch341` → `/dev/ttyUSB0` |
| Audio contestador | `AT+CREC` (WAV 8 kHz 16-bit en FAT interna `C:\User\…`) — no V.253 |

Firmware visto: `SIM800 R14.18` / `Revision:1418B05SIM800C24`.

## SIM / PIN

```bash
# Con el servicio parado:
sudo systemctl stop zeromatic
SIM_PIN=1234 /opt/zeromatic/venv/bin/python3 /opt/zeromatic/bin/gsm_setup_sim.py
sudo systemctl start zeromatic
```

El unit systemd admite `SIM_PIN` y `DISABLE_SIM_PIN=1` (definir el PIN real solo en un override local, no en el repo).

## Comprobar en la Pi

```bash
lsusb | grep -i 1a86
ls -l /dev/ttyUSB0
dmesg | grep -i ch341
```

Con el servicio parado:

```bash
sudo systemctl stop zeromatic
python3 - <<'PY'
import serial, time
s=serial.Serial('/dev/ttyUSB0',115200,timeout=0.3)
s.write(b'AT\r'); time.sleep(0.3); print(s.read(64))
s.write(b'ATI\r'); time.sleep(0.4); print(s.read(128))
s.write(b'AT+CPIN?\r'); time.sleep(0.5); print(s.read(128))
PY
sudo systemctl start zeromatic
```

## Legado: Conexant CX93010 (PSTN)

Si vuelves al módem USB Conexant (`0572:1340` → `/dev/ttyACM0`):

```ini
Environment=MODEM_TYPE=conexant
Environment=MODEM_DEVICE=/dev/ttyACM0
```

Detalle del test 2026-10-24: [INFORME-hardware-modem-2026-10-24.md](../INFORME-hardware-modem-2026-10-24.md).
