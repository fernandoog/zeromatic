# Hardware

## Configuración de referencia

| Pieza | Detalle |
|-------|---------|
| SBC | Raspberry Pi Zero W (u otra Pi con USB host) |
| Módem | USB Conexant CX93010 (`0572:1340`) |
| Línea | PSTN analógica vía RJ11 (no IP phone “puro”) |
| Driver Linux | `cdc_acm` → `/dev/ttyACM0` |

## Qué debe cumplir el módem

- **Hardware modem** (controller-based), no softmodem.
- Chipset **Conexant CX93010** (o USR5637 controller voice).
- Soporte `AT+FCLASS=8` (voz / TAM) y audio V.253 (`+VTX` / `+VRX`).
- CDC-ACM nativo en Linux (sin drivers Windows).

## Comprobar en la Pi

```bash
lsusb | grep -i conexant
ls -l /dev/ttyACM0
dmesg | grep -i ttyACM
```

Firmware típico: `CX93001-EIS_V0.2013-V92` (`ATI3`).

## Test de hardware

Con el servicio parado:

```bash
sudo systemctl stop contestador
sudo /opt/contestador/venv/bin/python3 /opt/contestador/bin/hw_modem_test.py
sudo /opt/contestador/venv/bin/python3 /opt/contestador/bin/hw_voice_probe.py
sudo systemctl start contestador
```

Esperado: AT OK, `FCLASS` incluye `8`, `AT+VRX` → `CONNECT`, sin `NO DIALTONE` si hay línea.

## ¿Comprar otro?

**No**, si el actual responde como arriba. El CX93010 ya es el chipset correcto.

Repuesto barato (mismo chip): **StarTech USB56KEMH2** (~35–45 €).  
Evitar softmodems genéricos baratos en ARM/Pi.

Informe completo del test del 2026-10-24: [INFORME-hardware-modem-2026-10-24.md](../INFORME-hardware-modem-2026-10-24.md).
