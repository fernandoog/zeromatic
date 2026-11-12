#!/usr/bin/env python3
"""Módem GSM SIM800 (SIMCom) vía USB-UART (p. ej. CH340 → /dev/ttyUSB0).

Audio de contestador: no hay V.253. Locución y grabación van por AT+CREC.
La FAT interna es muy pequeña (~180 KiB libres); usamos AMR para saludo,
pitido y mensajes (WAV PCM solo deja ~5 s de grabación).
"""
from __future__ import annotations

import logging
import os
import struct
import subprocess
import time
import wave
from pathlib import Path

import serial

# Saludo en WAV PCM (lo que CREC reproduce bien en llamada). El pitido es corto.
# La grabación va en AMR nativo del SIM800: la FAT solo tiene ~150 KiB libres.
GREETING_REMOTE = os.environ.get("GSM_GREETING_PATH", r"C:\User\greeting.wav")
BEEP_REMOTE = os.environ.get("GSM_BEEP_PATH", r"C:\User\beep.wav")
RECORD_ID = int(os.environ.get("GSM_RECORD_ID", "2"))
# 0=AMR (compacto, ~1 KiB/s), 1=WAV PCM (~16 KiB/s → se llena en ~5 s)
RECORD_FORMAT = int(os.environ.get("GSM_RECORD_FORMAT", "0"))
SIM_PIN = os.environ.get("SIM_PIN", "").strip()
DISABLE_SIM_PIN = os.environ.get("DISABLE_SIM_PIN", "1").strip() not in {
    "0",
    "false",
    "no",
    "off",
}


class GsmModem:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        # CH340 / SIM800: sin RTS/CTS hardware
        self.ser = serial.Serial(port, baud, timeout=0.25, rtscts=False, dsrdtr=False)
        # Mantener DTR/RTS activos: con CSCLK=1 y DTR alto el módulo duerme y
        # deja de sacar RING por UART.
        try:
            self.ser.dtr = True
            self.ser.rts = True
        except Exception:
            pass
        time.sleep(0.35)
        self.ser.reset_input_buffer()
        self._line_buf = bytearray()
        self._last_keepalive = 0.0

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def _reopen(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
        time.sleep(0.8)
        self.ser = serial.Serial(
            self.port, self.baud, timeout=0.25, rtscts=False, dsrdtr=False
        )
        try:
            self.ser.dtr = True
            self.ser.rts = True
        except Exception:
            pass
        time.sleep(0.4)
        self._line_buf = bytearray()

    def _drain(self, seconds: float = 0.4) -> None:
        t0 = time.time()
        while time.time() - t0 < seconds:
            n = self.ser.in_waiting
            if n:
                self.ser.read(n)
            else:
                time.sleep(0.03)

    def cmd(self, command: str, wait: float = 2.0, expect_ok: bool = True) -> str:
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.write((command + "\r").encode("ascii", errors="ignore"))
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                u = buf.upper()
                if b"OK" in u or b"ERROR" in u:
                    time.sleep(0.08)
                    if self.ser.in_waiting:
                        buf += self.ser.read(self.ser.in_waiting)
                    break
            else:
                time.sleep(0.02)
        text = buf.decode("latin1", errors="replace")
        show = text.replace("\r", "\\r").replace("\n", "\\n")
        logging.info("AT >>> %s <<< %r", command, show[:140])
        if expect_ok and "ERROR" in text.upper() and "OK" not in text.upper():
            logging.warning("Modem ERROR en %s", command)
        return text

    def probe_at(self, wait: float = 1.2) -> bool:
        r = self.cmd("AT", wait=wait, expect_ok=False)
        return "OK" in r.upper()

    def _wait_cpin(self, timeout: float = 60.0) -> str:
        """Espera estado SIM. Devuelve READY / SIM PIN / SIM PUK / NOT INSERTED / …"""
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            r = self.cmd("AT+CPIN?", wait=2.0, expect_ok=False)
            last = r
            u = r.upper()
            if "READY" in u:
                return "READY"
            if "SIM PIN" in u:
                return "SIM PIN"
            if "SIM PUK" in u:
                return "SIM PUK"
            if "NOT INSERTED" in u or "SIM NOT INSERTED" in u:
                time.sleep(1.0)
                continue
            time.sleep(1.0)
        logging.error("Timeout CPIN: %r", last[:80])
        return "TIMEOUT"

    def unlock_and_optional_disable_pin(self, pin: str) -> None:
        if not pin:
            return
        st = self._wait_cpin(45.0)
        if st == "SIM PIN":
            logging.info("Desbloqueando SIM con PIN…")
            r = self.cmd(f'AT+CPIN="{pin}"', wait=5.0, expect_ok=False)
            if "OK" not in r.upper():
                raise RuntimeError(f"PIN rechazado: {r[:80]}")
            time.sleep(1.5)
            st = self._wait_cpin(30.0)
        if st != "READY":
            raise RuntimeError(f"SIM no READY tras PIN ({st})")
        if DISABLE_SIM_PIN:
            logging.info("Desactivando petición de PIN (CLCK SC)…")
            r = self.cmd(f'AT+CLCK="SC",0,"{pin}"', wait=6.0, expect_ok=False)
            if "OK" not in r.upper():
                logging.warning("No se pudo desactivar PIN: %r", r[:80])
            else:
                chk = self.cmd('AT+CLCK="SC",2', wait=3.0, expect_ok=False)
                logging.info("Estado lock SC: %r", chk.replace("\r", "\\r").replace("\n", "\\n")[:80])

    def wait_network(self, timeout: float = 90.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            r = self.cmd("AT+CREG?", wait=1.5, expect_ok=False)
            # 0,1 home ; 0,5 roaming
            if ",1" in r or ",5" in r:
                cops = self.cmd("AT+COPS?", wait=3.0, expect_ok=False)
                logging.info("Red OK: %s", cops.strip()[:80])
                return True
            time.sleep(2.0)
        logging.error("Sin registro de red (CREG) en %.0fs", timeout)
        return False

    def init(self) -> None:
        if not self.probe_at():
            self._drain(0.5)
            if not self.probe_at(wait=2.0):
                raise RuntimeError(f"Sin respuesta AT en {self.port}")
        self.cmd("ATE0", expect_ok=False)
        self.cmd("AT+CMEE=2", expect_ok=False)
        self.cmd("AT+CFUN=1", expect_ok=False)
        pin = SIM_PIN
        logging.info("Esperando SIM (hasta ~2 min)…")
        try:
            st = self._wait_cpin(90.0)
        except Exception:
            st = "TIMEOUT"
        if st == "SIM PIN":
            if not pin:
                raise RuntimeError("SIM pide PIN y SIM_PIN no está configurado")
            self.unlock_and_optional_disable_pin(pin)
        elif st == "READY":
            if pin and DISABLE_SIM_PIN:
                # Por si aún tiene PIN activo pero no lo pide en este boot
                self.unlock_and_optional_disable_pin(pin)
        elif st == "SIM PUK":
            raise RuntimeError("SIM bloqueada con PUK")
        else:
            raise RuntimeError(
                "SIM no detectada (AT+CPIN → not inserted / timeout). "
                "Asienta la nano/micro-SIM en el SIM800C (contactos hacia abajo "
                "según la marca de la bandeja) y reinicia el servicio."
            )

        self.wait_network(90.0)
        # Evitar sleep (sin RING por UART) y no depender de DTR del CH340.
        self.cmd("AT+CSCLK=0", expect_ok=False)
        self.cmd("AT&D0", expect_ok=False)
        self.cmd("ATS0=0", expect_ok=False)  # contestamos nosotros con ATA
        self.cmd("AT+CLIP=1", expect_ok=False)
        self.cmd("AT+CRC=1", expect_ok=False)  # +CRING: VOICE
        self.cmd("AT+CLCC=1", expect_ok=False)  # URC de cambio de estado de llamada
        self.cmd("AT+COLP=0", expect_ok=False)
        self.cmd("AT+CVHU=0", expect_ok=False)
        self.cmd("AT+CLVL=90", expect_ok=False)
        self.cmd("AT+CRSL=60", expect_ok=False)
        self.cmd("AT+CHFA=0", expect_ok=False)
        self.cmd("AT+DDET=0", expect_ok=False)  # DDET=1 bloquea CREC en llamada
        self.cmd("AT+VTD=8", expect_ok=False)  # DTMF más largo (fallback del pitido)
        self.sms_init()
        self._free_record_slots()
        self._drain(0.3)
        self._last_keepalive = time.time()
        logging.info("SIM800 listo en %s (espacio CREC=%s)", self.port, self.crec_free_bytes())

    def force_recover(self) -> None:
        logging.info("Recuperando SIM800…")
        self.cmd("ATH", wait=2.0, expect_ok=False)
        self.cmd("AT+CREC=5", wait=1.5, expect_ok=False)
        self.cmd("AT+CREC=2", wait=1.5, expect_ok=False)
        self._drain(0.4)
        if not self.probe_at():
            self._reopen()
        self.cmd("ATE0", expect_ok=False)
        self.cmd("AT+CMEE=2", expect_ok=False)
        self.cmd("AT+CSCLK=0", expect_ok=False)
        self.cmd("AT&D0", expect_ok=False)
        self.cmd("AT+CLIP=1", expect_ok=False)
        self.cmd("AT+CRC=1", expect_ok=False)
        self.cmd("AT+CLCC=1", expect_ok=False)
        self.cmd("ATS0=0", expect_ok=False)
        self.sms_init()
        self._last_keepalive = time.time()

    def hangup(self) -> None:
        self.cmd("ATH", wait=2.0, expect_ok=False)
        self.cmd("AT+CHUP", wait=2.0, expect_ok=False)
        self._drain(0.3)

    def answer_now(self) -> None:
        logging.info("Descolgando (ATA)")
        r = self.cmd("ATA", wait=5.0, expect_ok=False)
        if "OK" not in r.upper() and "ERROR" in r.upper():
            logging.warning("ATA falló (%r) — reintento", r[:60])
            time.sleep(0.4)
            self.cmd("ATA", wait=5.0, expect_ok=False)
        time.sleep(0.4)

    @staticmethod
    def _clcc_is_incoming(line: str) -> bool:
        """+CLCC: <id>,<dir>,<stat>,…  stat 4=incoming 5=waiting; dir 1=MT."""
        u = line.strip().upper()
        if not u.startswith("+CLCC:"):
            return False
        body = u.split(":", 1)[1].strip()
        parts = [p.strip() for p in body.split(",")]
        if len(parts) < 3:
            return False
        try:
            direction = int(parts[1])
            state = int(parts[2])
        except ValueError:
            return False
        return direction == 1 and state in (4, 5)

    def keepalive(self, every: float = 20.0) -> None:
        """Un AT periódico evita que el SIM800 deje de empujar URCs RING."""
        now = time.time()
        if now - self._last_keepalive < every:
            return
        self._last_keepalive = now
        # No resetear buffer: puede haber RING a punto de llegar.
        self.write(b"AT\r")
        # Dejar la respuesta OK para que read_event la consuma como line.

    def poll_incoming(self) -> tuple[str, str]:
        """Consulta AT+CLCC por si se perdió el URC RING.

        Devuelve el bloque completo de respuesta (puede incluir +CLIP).
        """
        r = self.cmd("AT+CLCC", wait=1.5, expect_ok=False)
        for line in r.splitlines():
            if self._clcc_is_incoming(line.strip()):
                return "ring", r
        return "", ""

    def fetch_caller_id(self) -> str:
        """Último intento de obtener el número (CLCC + CLIP pendientes)."""
        # Primero drenar URCs ya en el buffer (CLIP entre timbres)
        got = ""
        end = time.time() + 0.35
        while time.time() < end:
            n = self.ser.in_waiting
            if not n:
                time.sleep(0.03)
                continue
            chunk = self.ser.read(n).decode("latin1", errors="replace")
            got = parse_caller_from_urc(chunk) or got
        r = self.cmd("AT+CLCC", wait=1.2, expect_ok=False)
        return parse_caller_from_urc(r) or got

    def read_event(self, timeout: float = 1.0) -> tuple[str, str]:
        """Devuelve (tipo, texto). tipo: ring | line | ''."""
        self.keepalive()
        end = time.time() + timeout
        while time.time() < end:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._line_buf.extend(chunk)
            elif not self._line_buf:
                time.sleep(0.03)
                continue
            while True:
                raw = bytes(self._line_buf)
                sep = -1
                for i, b in enumerate(raw):
                    if b in (10, 13):
                        sep = i
                        break
                if sep < 0:
                    break
                line = raw[:sep].decode("latin1", errors="replace").strip()
                # consume line + following CR/LF
                j = sep
                while j < len(raw) and raw[j] in (10, 13):
                    j += 1
                del self._line_buf[:j]
                if not line:
                    continue
                u = line.upper()
                if u == "RING" or u.startswith("+CRING"):
                    return "ring", line
                if self._clcc_is_incoming(line):
                    return "ring", line
                if u.startswith("+CMTI:"):
                    return "sms", line
                if u.startswith("+CLIP:"):
                    return "line", line
                return "line", line
        return "", ""

    def sms_init(self) -> None:
        """Modo texto + indicación de SMS nuevo en SIM."""
        self.cmd("AT+CMGF=1", expect_ok=False)  # text mode
        self.cmd('AT+CSCS="GSM"', expect_ok=False)
        self.cmd('AT+CPMS="SM","SM","SM"', expect_ok=False)
        # Nuevos SMS → +CMTI: "SM",<index> (guardados en SIM)
        self.cmd("AT+CNMI=2,1,0,0,0", expect_ok=False)
        logging.info("SMS: CMGF=1 CNMI listo")

    def sms_list_indices(self) -> list[int]:
        """Índices de SMS en la SIM (todos)."""
        r = self.cmd('AT+CMGL="ALL"', wait=8.0, expect_ok=False)
        ids: list[int] = []
        for line in r.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = line.strip()
            if line.upper().startswith("+CMGL:"):
                body = line.split(":", 1)[1].strip()
                num = body.split(",", 1)[0].strip()
                try:
                    ids.append(int(num))
                except ValueError:
                    continue
        return ids

    def sms_read(self, index: int) -> dict[str, str] | None:
        """Lee un SMS. Devuelve {index,status,sender,time,text} o None."""
        r = self.cmd(f"AT+CMGR={index}", wait=5.0, expect_ok=False)
        if "ERROR" in r.upper() and "+CMGR:" not in r.upper():
            return None
        lines = [ln.strip() for ln in r.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        header = ""
        body_lines: list[str] = []
        seen = False
        for ln in lines:
            if not ln or ln.upper() in {"OK", "ERROR"}:
                continue
            if ln.upper().startswith("+CMGR:"):
                header = ln
                seen = True
                continue
            if seen:
                body_lines.append(ln)
        if not header:
            return None
        # +CMGR: "REC UNREAD","+346…","","yy/mm/dd,hh:mm:ss+zz"
        parts: list[str] = []
        cur = ""
        in_q = False
        payload = header.split(":", 1)[1].strip()
        for ch in payload:
            if ch == '"':
                in_q = not in_q
                cur += ch
            elif ch == "," and not in_q:
                parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur:
            parts.append(cur.strip())

        def unquote(s: str) -> str:
            s = s.strip()
            if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
                return s[1:-1]
            return s

        status = unquote(parts[0]) if parts else ""
        sender = unquote(parts[1]) if len(parts) > 1 else ""
        when = unquote(parts[3]) if len(parts) > 3 else ""
        text = decode_sms_body("\n".join(body_lines).strip())
        return {
            "index": str(index),
            "status": status,
            "sender": sender,
            "time": when,
            "text": text,
        }

    def sms_delete(self, index: int) -> bool:
        r = self.cmd(f"AT+CMGD={index}", wait=3.0, expect_ok=False)
        ok = "OK" in r.upper() and "ERROR" not in r.upper()
        if ok:
            logging.info("SMS %d borrado de la SIM", index)
        else:
            logging.warning("No se pudo borrar SMS %d: %r", index, r[:80])
        return ok

    def parse_cmti_index(self, line: str) -> int | None:
        """+CMTI: \"SM\",3 → 3"""
        u = line.strip().upper()
        if not u.startswith("+CMTI:"):
            return None
        try:
            tail = line.split(",", 1)[1].strip()
            return int(tail)
        except (IndexError, ValueError):
            return None

    def _fswrite_chunk(self, remote: str, mode: int, payload: bytes, timeout_s: int = 30) -> None:
        """Una escritura FSWRITE (máx. 10240 bytes por comando SIM800)."""
        if not payload:
            return
        if len(payload) > 10240:
            raise ValueError(f"FSWRITE chunk demasiado grande: {len(payload)}")
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        cmd = f'AT+FSWRITE="{remote}",{mode},{len(payload)},{timeout_s}'
        logging.info("AT >>> %s", cmd)
        self.write((cmd + "\r").encode("ascii"))
        end = time.time() + 8
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
                if b"ERROR" in buf.upper():
                    raise RuntimeError(f"FSWRITE rechazado: {buf[:80]!r}")
            else:
                time.sleep(0.03)
        if b">" not in buf:
            raise RuntimeError(f"Sin prompt FSWRITE: {buf[:80]!r}")
        self.write(payload)
        end = time.time() + float(timeout_s) + 5
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(512)
            if chunk:
                buf += chunk
                if b"OK" in buf or b"ERROR" in buf.upper():
                    break
            else:
                time.sleep(0.05)
        if b"OK" not in buf:
            raise RuntimeError(f"FSWRITE incompleto: {buf[:80]!r}")

    def crec_free_bytes(self) -> int:
        r = self.cmd("AT+CREC=8", wait=2.0, expect_ok=False)
        for part in r.splitlines():
            if part.startswith("+CREC: 8,"):
                body = part.split(",", 1)[1].strip()
                # "+CREC: 8,147968" o "sys:… sd:…"
                num = "".join(ch for ch in body.split()[0] if ch.isdigit())
                if num:
                    try:
                        return int(num)
                    except ValueError:
                        return 0
        return 0

    def _free_record_slots(self) -> None:
        """Borra slots CREC viejos (p. ej. WAV enormes) para recuperar flash."""
        for rid in range(1, 11):
            self.cmd(f"AT+CREC=3,{rid}", wait=1.5, expect_ok=False)
        # Limpia WAV legacy del saludo si quedó de una versión anterior
        self.cmd(f'AT+FSDEL="C:\\User\\greeting.wav"', wait=1.5, expect_ok=False)

    def upload_wav(self, local_wav: Path, remote: str = GREETING_REMOTE) -> None:
        """Sube audio a la FAT del SIM800 (AMR preferido; chunks ≤10240)."""
        data = ensure_gsm_remote_bytes(local_wav, remote)
        self.cmd(f'AT+FSDEL="{remote}"', wait=2.0, expect_ok=False)
        r = self.cmd(f'AT+FSCREATE="{remote}"', wait=3.0, expect_ok=False)
        if "OK" not in r.upper():
            raise RuntimeError(f"FSCREATE falló: {r[:80]}")
        chunk_size = 10240
        offset = 0
        first = True
        while offset < len(data):
            piece = data[offset : offset + chunk_size]
            self._fswrite_chunk(remote, 0 if first else 1, piece)
            first = False
            offset += len(piece)
        sz = self.cmd(f'AT+FSFLSIZE="{remote}"', wait=2.0, expect_ok=False)
        logging.info(
            "Audio subido (%d bytes) %s → %s (CREC libre=%d)",
            len(data),
            local_wav,
            sz.strip()[:60],
            self.crec_free_bytes(),
        )

    def upload_prompt_files(self, greeting_wav: Path) -> None:
        """Sube saludo WAV + pitido corto WAV a la FAT."""
        self.upload_wav(greeting_wav, GREETING_REMOTE)
        beep_path = Path("/tmp/zeromatic_beep.wav")
        beep_path.write_bytes(synth_beep_wav_bytes(freq=1000.0, seconds=0.55))
        self.upload_wav(beep_path, BEEP_REMOTE)

    def release_greeting_space(self) -> None:
        """Quita el saludo de la FAT para dejar sitio a la grabación AMR."""
        self.cmd(f'AT+FSDEL="{GREETING_REMOTE}"', wait=2.0, expect_ok=False)
        free = self.crec_free_bytes()
        logging.info("Saludo borrado de FAT; CREC libre=%d", free)

    def prepare_record_slot(self, rec_id: int = RECORD_ID) -> None:
        """Borra el slot CREC por adelantado (antes del pitido)."""
        self.cmd(f"AT+CREC=3,{rec_id}", wait=1.5, expect_ok=False)

    def play_remote(self, remote: str = GREETING_REMOTE, volume: int = 90) -> None:
        """Reproduce fichero en llamada (canal 0 = red)."""
        r = self.cmd(f'AT+CREC=4,"{remote}",0,{volume}', wait=3.0, expect_ok=False)
        if "OK" not in r.upper():
            logging.warning("CREC play falló (%r)", r[:60])
            return
        # Solo terminar con +CREC: 0 (no con cualquier ERROR de URC colateral)
        end = time.time() + 60
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                chunk = self.ser.read(n).decode("latin1", errors="replace")
                if "+CREC: 0" in chunk:
                    break
                if "+CME ERROR" in chunk.upper() and "CREC" in chunk.upper():
                    break
            else:
                time.sleep(0.1)
        self.cmd("AT+CREC=5", wait=0.8, expect_ok=False)

    def beep_dtmf(self) -> None:
        """Respaldo DTMF (a menudo inaudible en red; preferir beep_crec)."""
        self._drain(0.1)
        self.cmd("AT+VTD=10", expect_ok=False)
        self.cmd("AT+VTS=9", wait=2.5, expect_ok=False)
        time.sleep(0.05)

    def beep_crec(self) -> None:
        """Pitido WAV por la vía de voz (audible). No solapar con grabación."""
        self._drain(0.25)
        # Volumen al máximo del canal de red
        self.cmd("AT+CLVL=100", expect_ok=False)
        r = self.cmd(f'AT+CREC=4,"{BEEP_REMOTE}",0,100', wait=2.0, expect_ok=False)
        if "OK" not in r.upper():
            logging.warning("Pitido CREC falló (%r) — intento DTMF", r[:60])
            self.beep_dtmf()
            return
        # Ignorar +CREC: 0 residual: duración fija del tono (~0.55 s) + margen
        time.sleep(0.65)
        self.cmd("AT+CREC=5", wait=0.5, expect_ok=False)
        self._drain(0.05)

    def start_record(self, rec_id: int = RECORD_ID, max_sec: float = 35.0) -> bool:
        """Arranca CREC lo antes posible. Devuelve True si OK."""
        fmt = RECORD_FORMAT
        limit = max(1, min(int(max_sec), 59))
        logging.info("Iniciando grabación CREC id=%d fmt=%d max=%ds", rec_id, fmt, limit)
        r = self.cmd(f"AT+CREC=1,{rec_id},{fmt},{limit}", wait=2.0, expect_ok=False)
        if "OK" not in r.upper():
            logging.error("No se pudo iniciar CREC: %r", r[:80])
            return False
        return True

    def wait_record_done(self, rec_id: int = RECORD_ID, max_sec: float = 35.0) -> int:
        """Espera fin de CREC y devuelve tamaño en bytes."""
        limit = max(1, min(int(max_sec), 59))
        end = time.time() + limit + 8
        size = 0
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                chunk = self.ser.read(n).decode("latin1", errors="replace")
                # +CREC: 2,<id>,<fmt>,<sec>,<bytes>
                if "+CREC: 2," in chunk:
                    for part in chunk.splitlines():
                        part = part.strip()
                        if part.startswith("+CREC: 2,"):
                            bits = part.split(",")
                            if len(bits) >= 5:
                                try:
                                    size = int(bits[4].strip())
                                except ValueError:
                                    size = 0
                            logging.info("CREC fin natural: %s", part[:80])
                    break
                if "NO CARRIER" in chunk.upper():
                    logging.info("NO CARRIER durante grabación — paro CREC")
                    self.cmd("AT+CREC=2", wait=2.0, expect_ok=False)
                    time.sleep(0.4)
                    break
            else:
                time.sleep(0.1)
        else:
            self.cmd("AT+CREC=2", wait=2.0, expect_ok=False)
            time.sleep(0.4)
        if size <= 0:
            info = self.cmd(f"AT+CREC=7,{rec_id}", wait=2.0, expect_ok=False)
            for part in info.splitlines():
                if part.startswith("+CREC: 7,"):
                    bits = part.split(",")
                    if len(bits) >= 3:
                        try:
                            size = int(bits[2].strip())
                        except ValueError:
                            pass
        logging.info("Grabación CREC id=%d → %d bytes", rec_id, size)
        return size

    def record_to_id(
        self, rec_id: int = RECORD_ID, max_sec: float = 35.0, prepare: bool = True
    ) -> int:
        """Compat: prepara (opcional), graba y espera fin."""
        if prepare:
            self.prepare_record_slot(rec_id)
        if not self.start_record(rec_id, max_sec=max_sec):
            return 0
        return self.wait_record_done(rec_id, max_sec=max_sec)

    def download_record(self, rec_id: int, out_wav: Path, total: int | None = None) -> int:
        """Descarga slot CREC (hex) y lo deja como WAV PCM 8 kHz para el pipeline."""
        fmt = RECORD_FORMAT
        if total is None or total <= 0:
            info = self.cmd(f"AT+CREC=7,{rec_id}", wait=2.0, expect_ok=False)
            total = 0
            for part in info.splitlines():
                if part.startswith("+CREC: 7,"):
                    bits = part.split(",")
                    if len(bits) >= 3:
                        try:
                            total = int(bits[2].strip())
                        except ValueError:
                            total = 0
                    if len(bits) >= 4:
                        try:
                            fmt = int(bits[3].strip())
                        except ValueError:
                            pass
        if total <= 0:
            out_wav.write_bytes(b"")
            return 0
        raw = bytearray()
        offset = 0
        chunk = 2048
        while offset < total:
            n = min(chunk, total - offset)
            r = self.cmd(f"AT+CREC=6,{rec_id},{n},{offset}", wait=8.0, expect_ok=False)
            hexdata = ""
            lines = r.replace("\r\n", "\n").split("\n")
            for i, line in enumerate(lines):
                if line.startswith("+CREC: 6,"):
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip():
                            hexdata = lines[j].strip()
                            break
                    break
            if not hexdata:
                logging.warning("CREC=6 sin datos en offset %d", offset)
                break
            try:
                piece = bytes.fromhex(hexdata)
            except ValueError:
                logging.warning("HEX inválido en offset %d", offset)
                break
            raw.extend(piece)
            offset += len(piece)
            if len(piece) == 0:
                break
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if fmt == 0:
            amr = out_wav.with_suffix(".amr")
            # Algunos SIM800 entregan AMR sin cabecera "#!AMR\n"
            blob = bytes(raw)
            if not blob.startswith(b"#!AMR"):
                blob = b"#!AMR\n" + blob
            amr.write_bytes(blob)
            try:
                subprocess.check_call(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "amr",
                        "-i",
                        str(amr),
                        "-ar",
                        "8000",
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(out_wav),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                logging.exception("ffmpeg AMR→WAV falló")
                out_wav.write_bytes(b"")
                return 0
            try:
                amr.unlink(missing_ok=True)
            except Exception:
                pass
            n = out_wav.stat().st_size if out_wav.is_file() else 0
            logging.info("Descargado AMR→WAV %s (%d bytes raw → %d wav)", out_wav, len(raw), n)
            return n
        out_wav.write_bytes(bytes(raw))
        logging.info("Descargado %s (%d bytes)", out_wav, len(raw))
        return len(raw)

    def dial_voice(self, number: str, timeout: float = 45.0) -> bool:
        dial = f"ATD{number};"
        logging.info("Marcando %s …", number)
        self._drain(0.2)
        self.write((dial + "\r").encode("ascii"))
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                u = buf.upper()
                if b"NO CARRIER" in u or b"BUSY" in u or b"NO ANSWER" in u:
                    logging.error("Marcación fallida: %r", buf[:100])
                    return False
                if b"OK" in u and b"ERROR" not in u:
                    logging.info("Marcación OK")
                    return True
            else:
                time.sleep(0.05)
        logging.error("Timeout marcación")
        return False


def ensure_gsm_wav_bytes(path: Path) -> bytes:
    """WAV PCM 16-bit mono 8 kHz (formato que SIM800C reproduce en llamada)."""
    tmp = Path("/tmp/zeromatic_gsm_play.wav")
    subprocess.check_call(
        [
            "sox",
            str(path),
            "-r",
            "8000",
            "-c",
            "1",
            "-b",
            "16",
            "-e",
            "signed-integer",
            str(tmp),
            "vol",
            "3",
            "dB",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return tmp.read_bytes()


def ensure_amr_bytes_from_path(path: Path) -> bytes:
    """Convierte cualquier audio a AMR-NB 8 kHz (ocupa ~1.2 KiB/s en la FAT)."""
    if path.suffix.lower() == ".amr" and path.is_file() and path.stat().st_size > 32:
        return path.read_bytes()
    wav = ensure_gsm_wav_bytes(path)
    return ensure_amr_bytes_from_wav_bytes(wav)


def ensure_amr_bytes_from_wav_bytes(wav_bytes: bytes) -> bytes:
    src = Path("/tmp/zeromatic_gsm_amr_src.wav")
    dst = Path("/tmp/zeromatic_gsm_amr_out.amr")
    src.write_bytes(wav_bytes)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ar",
            "8000",
            "-ac",
            "1",
            "-c:a",
            "amr_nb",
            "-b:a",
            "12.2k",
            str(dst),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return dst.read_bytes()


def ensure_gsm_remote_bytes(path: Path, remote: str) -> bytes:
    """Elige AMR o WAV según la extensión remota en el SIM800."""
    if remote.lower().endswith(".amr"):
        return ensure_amr_bytes_from_path(path)
    if path.suffix.lower() == ".amr":
        # remoto WAV pero fuente AMR: decodificar vía ffmpeg a WAV PCM
        tmp = Path("/tmp/zeromatic_gsm_from_amr.wav")
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(tmp),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return tmp.read_bytes()
    return ensure_gsm_wav_bytes(path)


def parse_clip(line: str) -> str:
    """+CLIP: \"+34612…\",129 → número."""
    if '"' not in line:
        return ""
    try:
        q1 = line.find('"')
        q2 = line.find('"', q1 + 1)
        if q1 >= 0 and q2 > q1:
            return line[q1 + 1 : q2].strip()
    except Exception:
        pass
    return ""


def parse_clcc_number(line: str) -> str:
    """+CLCC: 1,1,4,0,0,\"600000000\",129,\"\" → número."""
    u = line.strip()
    if not u.upper().startswith("+CLCC:"):
        return ""
    # El MSISDN va entre comillas (primer campo entre comillas)
    return parse_clip(u)


def decode_sms_body(body: str) -> str:
    """Decodifica cuerpo SMS (texto o UCS2 hex)."""
    s = (body or "").strip()
    if (
        len(s) >= 4
        and len(s) % 4 == 0
        and all(c in "0123456789abcdefABCDEF" for c in s)
        and " " not in s
    ):
        try:
            return bytes.fromhex(s).decode("utf-16-be", errors="replace")
        except Exception:
            pass
    return body.strip()


def parse_caller_from_urc(text: str) -> str:
    """Extrae Caller-ID de un bloque con +CLIP / +CLCC / CRING."""
    if not text:
        return ""
    best = ""
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        u = line.upper()
        if u.startswith("+CLIP:"):
            n = parse_clip(line)
            if n:
                return n
        if u.startswith("+CLCC:"):
            n = parse_clcc_number(line)
            if n:
                best = n
    return best


def synth_beep_wav_bytes(freq: float = 1000.0, seconds: float = 0.55, rate: int = 8000) -> bytes:
    import math

    n = int(rate * seconds)
    pcm = bytearray()
    # Casi a saturación para que se oiga claro en la llamada GSM
    amp = 30000
    for i in range(n):
        # Pequeño fade in/out para evitar click
        env = 1.0
        fade = int(rate * 0.02)
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = max(0.0, (n - i) / fade)
        v = int(amp * env * math.sin(2 * math.pi * freq * i / rate))
        pcm += struct.pack("<h", max(-32767, min(32767, v)))
    data_size = len(pcm)
    hdr = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", data_size)
    return hdr + bytes(pcm)
