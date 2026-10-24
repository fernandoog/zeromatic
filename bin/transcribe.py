#!/usr/bin/env python3
"""Transcribe WAV a texto (español). Prefiere OpenAI Whisper API; si no, Google Web Speech."""
from __future__ import annotations

import os
import sys
from pathlib import Path

wav = Path(os.environ.get("CONTESTADOR_WAV", ""))
out = Path(os.environ.get("CONTESTADOR_TXT", "/tmp/transcripcion.txt"))


def write(text: str) -> None:
    out.write_text(text.strip() + "\n", encoding="utf-8")
    print(text)


def via_openai(path: Path) -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        # cargar desde email.env si existe
        env = Path("/etc/contestador/email.env")
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        with path.open("rb") as f:
            tr = client.audio.transcriptions.create(
                model="whisper-1", file=f, language="es"
            )
        return (tr.text or "").strip()
    except Exception as e:
        print(f"OpenAI falló: {e}", file=sys.stderr)
        return None


def via_google(path: Path) -> str | None:
    try:
        import speech_recognition as sr
    except ImportError:
        print("speech_recognition no instalado", file=sys.stderr)
        return None
    r = sr.Recognizer()
    try:
        with sr.AudioFile(str(path)) as source:
            audio = r.record(source)
        return r.recognize_google(audio, language="es-ES")
    except sr.UnknownValueError:
        return "(no se entendió el audio)"
    except Exception as e:
        print(f"Google STT falló: {e}", file=sys.stderr)
        return None


def main() -> int:
    if not wav.is_file() or wav.stat().st_size == 0:
        write("(sin audio para transcribir)")
        return 1
    text = via_openai(wav) or via_google(wav)
    if not text:
        write("(transcripción no disponible)")
        return 1
    write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
