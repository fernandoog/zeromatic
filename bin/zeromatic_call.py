#!/opt/zeromatic/venv/bin/python3
"""
zeromatic <telefono> <mensaje...>

Marca con el mÃ³dem Conexant CX930xx (V.253), reproduce el texto (TTS) y cuelga.
Para el contestador mientras dura la llamada y lo vuelve a arrancar al final.

Uso:
  zeromatic 612345678 Hola, esto es una prueba
  zeromatic --dry-run 612345678 Solo genera el WAV
  ZEROMATIC_TIMEOUT=45 zeromatic 912345678 Mensaje largo
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import serial

DEVICE = os.environ.get("MODEM_DEVICE", "/dev/ttyACM0")
BAUD = int(os.environ.get("MODEM_BAUD", "115200"))
TIMEOUT = float(os.environ.get("ZEROMATIC_TIMEOUT", "60"))
# Segundos tras OK de marcaciÃ³n antes de hablar (tiempo de timbre + descolgar)
ANSWER_WAIT = float(os.environ.get("ZEROMATIC_ANSWER_WAIT", "12"))
SERVICE = os.environ.get("ZEROMATIC_SERVICE", "zeromatic")
TMP_DIR = Path(os.environ.get("ZEROMATIC_TMP", "/tmp/zeromatic"))
_default_log = Path.home() / ".local" / "log" / "zeromatic_call.log"
LOG = Path(os.environ.get("ZEROMATIC_LOG", str(_default_log)))
_SUDO_PASS_FILES = (
    Path(os.environ.get("ZEROMATIC_SUDO_FILE", "")),
    Path.home() / ".config" / "zeromatic" / "sudo_password",
    Path("/etc/zeromatic/sudo_password"),
    Path("/etc/contestador/sudo_password"),
)

DLE = 0x10
ETX = 0x03


def setup_log() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)],
    )


def sanitize_number(raw: str) -> str:
    """Solo dÃ­gitos, +, * # y comas de pausa Hayes."""
    n = re.sub(r"[^\d+*#,]", "", raw.strip())
    if not n or not re.search(r"\d", n):
        raise SystemExit(f"NÃºmero invÃ¡lido: {raw!r}")
    if len(re.sub(r"\D", "", n)) > 20:
        raise SystemExit("NÃºmero demasiado largo")
    return n


def _sudo_password() -> str | None:
    env = os.environ.get("ZEROMATIC_SUDO_PASSWORD", "").strip()
    if env:
        return env
    for path in _SUDO_PASS_FILES:
        if not path or not str(path):
            continue
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return None


def systemctl(*args: str) -> None:
    """systemctl con sudo no interactivo (password vÃ­a env/fichero si hace falta)."""
    try:
        subprocess.check_call(["systemctl", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    try:
        subprocess.check_call(
            ["sudo", "-n", "systemctl", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    pw = _sudo_password()
    if not pw:
        raise RuntimeError(
            "systemctl necesita sudo. Define ZEROMATIC_SUDO_PASSWORD "
            "o crea ~/.config/zeromatic/sudo_password (chmod 600)."
        )
    proc = subprocess.run(
        ["sudo", "-S", "-p", "", "systemctl", *args],
        input=(pw + "\n").encode(),
        capture_output=True,
        timeout=45,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"sudo systemctl {' '.join(args)} fallÃ³: {err}")


def stop_contestador() -> None:
    if os.environ.get("ZEROMATIC_SKIP_SERVICE") == "1":
        logging.warning("ZEROMATIC_SKIP_SERVICE=1 â€” no paro %s", SERVICE)
        return
    logging.info("Parando %sâ€¦", SERVICE)
    try:
        systemctl("stop", SERVICE)
    except Exception as e:
        logging.warning("stop fallÃ³ (%s) â€” kill forzado", e)
        try:
            systemctl("kill", "-s", "SIGKILL", SERVICE)
        except Exception as e2:
            logging.error("kill tambiÃ©n fallÃ³: %s", e2)
    time.sleep(1.5)


def start_contestador() -> None:
    if os.environ.get("ZEROMATIC_SKIP_SERVICE") == "1":
        return
    logging.info("Arrancando %sâ€¦", SERVICE)
    try:
        systemctl("start", SERVICE)
    except Exception as e:
        logging.error("No pude arrancar %s: %s", SERVICE, e)


def tts_wav(text: str, out: Path) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    raw = TMP_DIR / "tts_raw.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        ["espeak-ng", "-v", "es", "-s", "140", "-a", "200", "-w", str(raw), text],
    )
    subprocess.check_call(
        ["sox", str(raw), "-r", "8000", "-c", "1", "-b", "16", str(out), "vol", "4", "dB"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def wav_to_u8_8k(path: Path) -> bytes:
    tmp = TMP_DIR / "play_u8.wav"
    subprocess.check_call(
        [
            "sox",
            str(path),
            "-r",
            "8000",
            "-c",
            "1",
            "-b",
            "8",
            "-e",
            "unsigned-integer",
            str(tmp),
            "vol",
            "6",
            "dB",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with wave.open(str(tmp), "rb") as w:
        return w.readframes(w.getnframes())


def shield_dle(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


class Modem:
    def __init__(self, port: str, baud: int) -> None:
        # Sin write_timeout: en VTX el ACM a veces tarda; el ritmo lo marca sleep
        self.ser = serial.Serial(port, baud, timeout=0.2, rtscts=True)

        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def write(self, data: bytes, flush: bool = True) -> None:
        self.ser.write(data)
        if flush:
            try:
                self.ser.flush()
            except serial.SerialTimeoutException:
                logging.warning("flush serie timeout (sigo)")

    def drain(self, seconds: float = 0.5) -> bytes:
        end = time.time() + seconds
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(512)
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.02)
        return bytes(buf)

    def cmd(self, command: str, wait: float = 2.0) -> str:
        self.drain(0.15)
        logging.info("AT >>> %s", command)
        self.write((command + "\r").encode("ascii"))
        end = time.time() + wait
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
                low = bytes(buf).upper()
                if b"OK" in low or b"ERROR" in low or b"VCON" in low or b"CONNECT" in low:
                    if b"BUSY" in low or b"NO CARRIER" in low or b"NO DIALTONE" in low or b"NO ANSWER" in low:
                        break
                    # un poco mÃ¡s por si llega el resto
                    time.sleep(0.15)
                    buf.extend(self.ser.read(self.ser.in_waiting or 0))
                    break
            else:
                time.sleep(0.02)
        text = bytes(buf).decode("ascii", errors="ignore")
        logging.info("AT <<< %r", text.replace("\r", "\\r").replace("\n", "\\n")[:120])
        return text

    def init_voice(self) -> None:
        """Misma secuencia que bin/test_call.py (probado en CX93010)."""
        self.write(b"\r")
        time.sleep(0.2)
        self.drain(0.3)
        for _ in range(2):
            self.write(bytes([DLE, ETX]))
            time.sleep(0.05)
        self.drain(0.3)
        self.cmd("ATH", wait=2.0)
        self.cmd("ATZ", wait=3.0)
        self.cmd("ATE0", wait=1.5)
        self.cmd("ATQ0V1", wait=1.5)
        self.cmd("ATX4", wait=1.5)
        self.cmd("AT+FCLASS=8", wait=2.0)
        self.cmd("ATS0=0", wait=1.5)
        # Path de lÃ­nea ANTES de marcar (crÃ­tico en este firmware)
        self.cmd("AT+VLS=1", wait=2.0)

    def dial_voice(self, number: str, timeout: float) -> bool:
        # ATDâ€¦; = llamada de voz (como test_call.py)
        dial = f"ATD{number};"
        self.drain(0.2)
        logging.info("Marcando %s â€¦", number)
        self.write((dial + "\r").encode("ascii"))
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
                u = bytes(buf).upper()
                if b"BUSY" in u:
                    logging.error("Ocupado")
                    return False
                if b"NO DIALTONE" in u or b"NO DIAL TONE" in u:
                    logging.error("Sin tono de marcaciÃ³n")
                    return False
                if b"NO ANSWER" in u:
                    logging.error("No contestan")
                    return False
                if b"NO CARRIER" in u:
                    logging.error("No carrier / colgaron")
                    return False
                if b"VCON" in u or b"CONNECT" in u or (
                    b"OK" in u and b"ERROR" not in u and len(buf) > 2
                ):
                    logging.info("MarcaciÃ³n OK (%r)", bytes(buf)[:80])
                    return True
            else:
                time.sleep(0.05)
        logging.error("Timeout esperando marcaciÃ³n (%.0fs)", timeout)
        return False

    def play_pcm_u8(self, pcm: bytes) -> bool:
        """Prueba varios VLS como test_call.py hasta que VTX acepte."""
        payload = shield_dle(pcm)
        for vls in ("1", "5", "13", "4"):
            self.cmd(f"AT+VLS={vls}", wait=2.0)
            self.cmd("AT+VSM=1", wait=2.0)
            r = self.cmd("AT+VTX", wait=4.0)
            u = r.upper()
            if "ERROR" in u and "CONNECT" not in u and "OK" not in u:
                logging.warning("AT+VTX rechazado con VLS=%s", vls)
                continue
            time.sleep(0.35)
            # Escritura por fd OS (evita bloqueos raros de pyserial+CTS en VTX saliente)
            fd = self.ser.fileno()
            chunk = 512
            for i in range(0, len(payload), chunk):
                part = payload[i : i + chunk]
                view = memoryview(part)
                while len(view):
                    n = os.write(fd, view)
                    view = view[n:]
                time.sleep(len(part) / 8000.0 * 0.85)
            os.write(fd, bytes([DLE, ETX]))
            time.sleep(0.3)
            self.drain(0.5)
            logging.info("LocuciÃ³n enviada VLS=%s (%d bytes PCM)", vls, len(pcm))
            return True
        logging.warning("VTX fallÃ³ â€” beep VTS de respaldo")
        self.cmd("AT+VLS=1", wait=2.0)
        self.cmd("AT+VTS=[933,0,800]", wait=5.0)
        return False

    def hangup(self) -> None:
        for _ in range(4):
            try:
                os.write(self.ser.fileno(), bytes([DLE, ETX]))
            except Exception:
                pass
            time.sleep(0.08)
        self.drain(0.4)
        # Tras VTX, flush/CTS puede colgar: mandar ATH en crudo
        try:
            os.write(self.ser.fileno(), b"ATH\r")
            time.sleep(0.5)
            os.write(self.ser.fileno(), b"AT+FCLASS=0\r")
            time.sleep(0.5)
        except Exception as e:
            logging.warning("hangup crudo: %s", e)
        self.drain(0.5)


def main() -> int:
    setup_log()
    p = argparse.ArgumentParser(
        prog="zeromatic",
        description="Marca, dice un texto (TTS) y cuelga (mÃ³dem Zeromatic / contestador).",
    )
    p.add_argument("--dry-run", action="store_true", help="Solo TTS; no marca ni toca el mÃ³dem")
    p.add_argument("--timeout", type=float, default=TIMEOUT, help="Segundos mÃ¡ximos de marcaciÃ³n")
    p.add_argument(
        "--wait",
        type=float,
        default=ANSWER_WAIT,
        help="Segundos tras marcar antes de hablar (timbre + descolgar)",
    )
    p.add_argument("telefono", help="NÃºmero a marcar (dÃ­gitos; se admite + y , de pausa)")
    p.add_argument("mensaje", nargs="+", help="Texto a sintetizar y reproducir")
    args = p.parse_args()

    number = sanitize_number(args.telefono)
    text = " ".join(args.mensaje).strip()
    if not text:
        raise SystemExit("Mensaje vacÃ­o")

    wav = TMP_DIR / "outbound.wav"
    logging.info("TTS: %r â†’ %s", text, wav)
    tts_wav(text, wav)

    if args.dry_run:
        logging.info("Dry-run OK (%s, %d bytes)", wav, wav.stat().st_size)
        print(wav)
        return 0

    stopped = False
    modem: Modem | None = None

    def _cleanup(*_a) -> None:  # noqa: ANN002
        nonlocal modem
        if modem:
            try:
                modem.hangup()
            except Exception:
                pass
            try:
                modem.close()
            except Exception:
                pass
            modem = None
        if stopped:
            start_contestador()

    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(1)))
    signal.signal(signal.SIGINT, lambda *_: (_cleanup(), sys.exit(1)))

    try:
        stop_contestador()
        stopped = True
        time.sleep(1.0)
        modem = Modem(DEVICE, BAUD)
        modem.init_voice()
        if not modem.dial_voice(number, timeout=args.timeout):
            modem.hangup()
            return 2
        logging.info("Esperando %.0fs a que contestenâ€¦", args.wait)
        time.sleep(args.wait)
        pcm = wav_to_u8_8k(wav)
        ok = modem.play_pcm_u8(pcm)
        time.sleep(0.5)
        modem.hangup()
        if ok:
            logging.info("Llamada terminada OK (TTS reproducido)")
            return 0
        logging.warning("Llamada hecha pero TTS no saliÃ³ por VTX")
        return 2
    except serial.SerialException as e:
        logging.error("Puerto serie: %s", e)
        return 3
    except Exception:
        logging.exception("Fallo en llamada")
        return 1
    finally:
        if modem:
            try:
                modem.close()
            except Exception:
                pass
        if stopped:
            start_contestador()


if __name__ == "__main__":
    sys.exit(main())
