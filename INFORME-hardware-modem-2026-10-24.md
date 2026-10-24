# Informe: test de hardware del módem USB (Zeromatic)

**Fecha:** 24 de octubre de 2026  
**Host:** Raspberry Pi Zero W (LAN local)  
**Kernel:** `6.18.50+rpt-rpi-v6`  
**Servicio:** `zeromatic` (daemon Python)  
**Objetivo:** verificar el estado físico/lógico del módem, contrastar especificaciones y decidir si conviene comprar otro.

---

## 1. Resumen ejecutivo

El módem **Conexant CX93010** está **operativo**. USB, comandos AT, modo voz (TAM), Caller ID y stream de audio (`AT+VRX`) responden correctamente. No aparece `NO DIALTONE`.

**Conclusión:** no es necesario comprar otro módem. El chipset ya es el adecuado para contestador analógico en Linux. Comprar un sustituto solo tendría sentido como **repuesto**, no como mejora.

| Aspecto | Veredicto |
|---------|-----------|
| Hardware USB / driver `cdc_acm` | OK |
| Firmware / identificación AT | OK |
| Capacidades voz (FCLASS=8, VSM, VLS) | OK |
| Línea PSTN (RJ11) | OK (sin `NO DIALTONE`) |
| Grabación audio (`AT+VRX`) | OK (`CONNECT` + PCM) |
| Sustitución recomendada | **No** |

---

## 2. Inventario detectado

| Campo | Valor |
|-------|--------|
| USB ID | `0572:1340` |
| Producto `lsusb` | Conexant Systems CX93010 ACF Modem |
| Nodo | `/dev/ttyACM0` (`root:dialout`) |
| Driver | `cdc_acm` |
| `ATI` / producto | `56000` |
| Firmware (`ATI3` / `AT+GMR`) | `CX93001-EIS_V0.2013-V92` |
| Fabricante (`AT+GMI`) | `CONEXANT` |
| Modelo (`AT+GMM`) | `V90` |

Nota: el descriptor USB dice CX93010 y el firmware reporta CX93001; es habitual en esta familia UCM/CX930xx.

---

## 3. Metodología del test

1. Parada temporal de `zeromatic.service` (el daemon monopoliza `/dev/ttyACM0`).
2. Script `bin/hw_modem_test.py`: identificación AT, clases, perfil, tono, off-hook, voz.
3. Script `bin/hw_voice_probe.py`: capacidades TAM profundas (`+VSM`, `+VLS`, `+VTX`/`+VRX`).
4. Rearranque del servicio y comprobación de estado activo.

Herramientas en el repositorio:

- `bin/hw_modem_test.py`
- `bin/hw_voice_probe.py`
- (relacionados) `bin/diag_quick.py`, `bin/diag_line.py`, `bin/full_test.sh`

---

## 4. Resultados del test

### 4.1 Identificación y perfil

- `ATH`, `ATZ`, `ATE0`, `ATQ0V1` → OK  
- Perfil activo (`AT&V`): `X4`, control de flujo `&K3`, etc.  
- `AT+GSN` / `AT+FMI?` → ERROR (no implementados en este firmware; irrelevante)

### 4.2 Capacidades data / fax / voz

| Comando | Respuesta | Interpretación |
|---------|-----------|----------------|
| `AT+FCLASS=?` | `0,1,1.0,2,8` | Data, fax Class 1/2 y **voz (8)** |
| `AT+VCID=?` | `(0-2)` | Caller ID disponible |
| `AT+VSM=?` | PCM firmado/unsigned, IMA ADPCM, μ-law, A-law, 14-bit PCM @ 8 kHz | Codecs suficientes para contestador |
| `AT+VLS=?` | Incluye `1,"T"` (línea telefónica) y variantes TAD | Enrutado a línea PSTN OK |
| `AT+VSD=?` | `(0-255),(0-255)` | Detección de silencio configurable |

Códigos `+VSM` observados:

- `0` SIGNED PCM 8-bit  
- `1` UNSIGNED PCM 8-bit (el que usa el daemon: `AT+VSM=1`)  
- `129` IMA ADPCM  
- `130` UNSIGNED PCM  
- `131` Mu-Law  
- `132` A-Law  
- `133` 14-bit PCM  

### 4.3 Línea telefónica

| Prueba | Resultado |
|--------|-----------|
| `ATX4` + espera de tono | Sin `NO DIALTONE` |
| `ATH1` (off-hook) | OK |
| Modo voz `AT+VLS=1` + `ATD,` | OK |

Interpretación: el RJ11 lleva línea útil (o al menos el DAA no reporta ausencia de tono). Los problemas históricos de “sin tono” en sesiones anteriores eran de cableado/filtro, no del chip.

### 4.4 Audio (TAM)

| Prueba | Resultado |
|--------|-----------|
| `AT+VTX` sin línea seleccionada | `ERROR` (esperado) |
| `AT+FCLASS=8` + `AT+VLS=1` + `AT+VSM=1,8000,0,0` + `AT+VRX` | `CONNECT` y stream PCM continuo |

El stream de `VRX` confirma que el camino **línea → módem → USB** funciona. La reproducción hacia línea (`VTX`) ya se validó en test saliente previo (`full_test.sh` / `test_call.py` con locución OK).

### 4.5 Estado post-test

Tras las pruebas:

- `contestador` = **active**  
- Escuchando RING en `/dev/ttyACM0`

---

## 5. Especificaciones del chipset (referencia)

Según documentación Conexant UCMxx / CX93010-2x + SmartDAA CX20548:

| Característica | Especificación |
|----------------|----------------|
| Tipo | Módem **controller-based** (hardware), no softmodem |
| Interfaz host | USB 1.1/2.0 full-speed, CDC |
| Datos | hasta V.92 / 56 kbps (recepción teórica) |
| Fax | Class 1 / 1.0 / 2, ~14.4 kbps |
| Voz / TAM | Sí (modo contestador remoto) |
| DAA | SmartDAA (sin transformador clásico); operación worldwide |
| Alimentación | Por bus USB (adecuado para Pi Zero con un solo dongle) |
| Linux | `cdc_acm` nativo (kernels ≥ 3.16 LTS en la práctica) |

Para un contestador en Raspberry Pi, este perfil es el **objetivo correcto**: hardware modem + voz + ACM.

---

## 6. Comparativa: ¿merece la pena otro?

### 6.1 Mismo segmento (PSTN USB voice)

| Modelo | Chip | Voz Linux | Precio orientativo (ES/UE, 2026) | ¿Mejora frente al actual? |
|--------|------|-----------|----------------------------------|---------------------------|
| **Actual (genérico Conexant)** | CX93010 | Sí | — (ya comprado) | Referencia |
| StarTech USB56KEMH2 | CX93010-21Z | Sí | ~35–45 € | **No** (mismo chip) |
| AGPtek / genéricos “56K Voice CX93010” | CX93010 | Sí si hardware | ~30–40 € | Solo como repuesto |
| USRobotics USR5637 | controller voice | Excelente (Call Attendant) | ~70–110 € (discontinuado / usado) | Marginal; más caro |
| Softmodems baratos sin CDC | varios | Malo en ARM/Pi | baratos | **Empeora** |

### 6.2 Otro segmento (no comparable)

Dongles GSM/LTE (Huawei E175x, Quectel EC25, SIM7600) sirven para **SIM móvil** con dongle GSM / software de telefonía móvil. No sustituyen una línea fija RJ11: cambian de arquitectura.

### 6.3 Decisión

| Pregunta | Respuesta |
|----------|-----------|
| ¿El módem actual está defectuoso? | **No** |
| ¿Hay un modelo barato claramente mejor? | **No** en PSTN USB |
| ¿Cuándo comprar? | Solo **repuesto** o si se cambia a GSM |
| Mejor repuesto barato | **StarTech USB56KEMH2** (~40 €) o genérico CX93010 hardware |

Los fallos observados en el proyecto (p. ej. `ATA` rechazado, necesidad de `AT+VLS` antes de contestar) son **peculiaridades de la secuencia AT del Conexant**, no indicios de hardware muerto. El daemon ya mitiga eso con modo voz permanente y enrutado `VLS`.

---

## 7. Recomendaciones operativas

1. **No comprar** otro módem por rendimiento.  
2. Si se quiere stock de seguridad: StarTech USB56KEMH2 o equivalente **hardware** Conexant (evitar softmodem).  
3. Mantener el cable RJ11 / filtro ADSL revisados: el síntoma `NO DIALTONE` suele ser de línea, no del USB.  
4. Para diagnóstico futuro: `sudo /opt/zeromatic/venv/bin/python3 /ruta/hw_modem_test.py` (parar zeromatic antes).  
5. No mezclar pruebas manuales (`minicom`, scripts) con el servicio activo: pelean por `ttyACM0`.

---

## 8. Anexos

### 8.1 Evidencia USB / dmesg (extracto)

```
usb 1-1: idVendor=0572, idProduct=1340
usb 1-1: Product: USB Modem
usb 1-1: Manufacturer: Conexant
cdc_acm 1-1:1.0: ttyACM0: USB ACM device
```

### 8.2 Comandos útiles

```bash
lsusb | grep -i conexant
ls -l /dev/ttyACM0
sudo systemctl stop zeromatic
sudo /opt/zeromatic/venv/bin/python3 /opt/zeromatic/bin/hw_modem_test.py
# o: /tmp/hw_voice_probe.py
sudo systemctl start zeromatic
sudo systemctl status zeromatic
```

### 8.3 Relación con tests previos (mismo día)

En la sesión de la mañana del 24/10/2026, `full_test.sh` reportó tono de línea OK y llamada saliente con locución OK (`AT+VTX` → `CONNECT`). El correo y la transcripción dependían de configuración (`GMAIL_APP_PASSWORD`, paquete `flac`), no del módem.

---

## 9. Conclusión

El hardware del Zeromatic es **apto y suficiente**. El CX93010 cumple las especificaciones necesarias (voz TAM, CDC-ACM, Caller ID, audio full-duplex vía V.253). Sustituirlo por otro USB 56K del mercado actual no aporta mejora técnica; como mucho conviene un **clon StarTech** de reserva (~40 €).
