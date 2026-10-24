#!/usr/bin/env python3
"""Genera greeting.wav con Kokoro-82M (ONNX), voz española local."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models" / "kokoro" / "kokoro-v1.0.onnx"
DEFAULT_VOICES = ROOT / "models" / "kokoro" / "voices-v1.0.bin"
DEFAULT_TEXT = "Hola. Deja tu mensaje después de la señal. Gracias."


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default="ef_dora", help="ef_dora | em_alex | em_santa")
    p.add_argument("--text", default=DEFAULT_TEXT)
    p.add_argument("--speed", type=float, default=0.95)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--voices", type=Path, default=DEFAULT_VOICES)
    p.add_argument("--out-dir", type=Path, default=ROOT / "media")
    args = p.parse_args()

    if not args.model.is_file() or not args.voices.is_file():
        print(f"Faltan modelos en {args.model.parent}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = args.out_dir / "greeting_kokoro_raw.wav"
    out = args.out_dir / "greeting.wav"

    print(f"Sintetizando con Kokoro voice={args.voice}…")
    kokoro = Kokoro(str(args.model), str(args.voices))
    samples, rate = kokoro.create(args.text, voice=args.voice, speed=args.speed, lang="es")
    sf.write(str(raw), samples, rate)

    # 8 kHz mono 16-bit para el módem V.253 (+ volumen línea)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-ar",
            "8000",
            "-ac",
            "1",
            "-acodec",
            "pcm_s16le",
            "-af",
            "volume=4dB",
            str(out),
        ]
    )
    print(f"OK {out} ({out.stat().st_size} bytes, {args.voice})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
