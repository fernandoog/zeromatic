#!/usr/bin/env python3
"""Envía correo con transcripción + audio adjunto vía Gmail SMTP."""
from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


def load_env(path: Path = Path("/etc/buzonera/email.env")) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path.exists():
        raise SystemExit(f"Falta {path}. Copia email.env.example y pon la App Password.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--audio", default="")
    args = ap.parse_args()

    cfg = load_env()
    mail_to = cfg.get("MAIL_TO", "").strip()
    mail_from = cfg.get("MAIL_FROM", cfg.get("GMAIL_USER", mail_to)).strip()
    user = cfg.get("GMAIL_USER", mail_from).strip()
    password = cfg.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not mail_to or not user:
        print("MAIL_TO / GMAIL_USER no configurados en email.env", file=sys.stderr)
        return 2
    if not password or password.startswith("xxxx"):
        print("GMAIL_APP_PASSWORD no configurada", file=sys.stderr)
        return 2

    meta = Path(args.meta).read_text(encoding="utf-8", errors="replace")
    transcript = Path(args.transcript).read_text(encoding="utf-8", errors="replace")

    def meta_field(name: str, default: str = "") -> str:
        prefix = name.lower() + ":"
        for line in meta.splitlines():
            if line.lower().startswith(prefix):
                return line.split(":", 1)[1].strip() or default
        return default

    def pretty_caller(raw: str) -> str:
        u = (raw or "").strip().upper()
        if not u or u in {"P", "O", "PRIVATE", "OUT_OF_AREA", "UNAVALIABLE", "UNAVAILABLE"}:
            return "número oculto"
        return raw.strip()

    caller = pretty_caller(meta_field("caller-id", "desconocido"))
    called = meta_field("called-id", "") or cfg.get("LINE_NUMBER", "") or cfg.get("CALLED_ID", "") or "no configurado"

    msg = EmailMessage()
    msg["Subject"] = f"[Buzonera] Mensaje de {caller}"
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(
        "Nuevo mensaje en Buzonera.\n\n"
        f"Quién llama (Caller-ID): {caller}\n"
        f"Línea contestada (Called-ID): {called}\n\n"
        f"{meta}\n"
        "--- Transcripción ---\n"
        f"{transcript}\n"
    )

    audio = Path(args.audio) if args.audio else None
    if audio and audio.is_file() and audio.stat().st_size > 0:
        ctype, _ = mimetypes.guess_type(str(audio))
        if not ctype:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            audio.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=audio.name,
        )

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
        s.starttls(context=ctx)
        s.login(user, password)
        s.send_message(msg)
    print(f"Correo enviado a {mail_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
