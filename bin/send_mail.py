#!/usr/bin/env python3
"""Envía correo (buzón de voz o SMS reenviados) vía Gmail SMTP."""
from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


def load_env(path: Path = Path("/etc/zeromatic/email.env")) -> dict[str, str]:
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


def send_gmail(
    subject: str,
    body: str,
    *,
    mail_to: str | None = None,
    attachments: list[Path] | None = None,
    cfg: dict[str, str] | None = None,
) -> None:
    """Envía un correo. Lanza excepción si falla SMTP/auth."""
    cfg = cfg or load_env()
    to_addr = (mail_to or cfg.get("MAIL_TO", "")).strip()
    mail_from = cfg.get("MAIL_FROM", cfg.get("GMAIL_USER", to_addr)).strip()
    user = cfg.get("GMAIL_USER", mail_from).strip()
    password = cfg.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not to_addr or not user:
        raise RuntimeError("MAIL_TO / GMAIL_USER no configurados en email.env")
    if not password or password.startswith("xxxx"):
        raise RuntimeError("GMAIL_APP_PASSWORD no configurada")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = to_addr
    msg.set_content(body)

    for audio in attachments or []:
        if not audio.is_file() or audio.stat().st_size <= 0:
            continue
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
    print(f"Correo enviado a {to_addr}")


def send_sms_forward(sender: str, when: str, text: str, mail_to: str | None = None) -> None:
    """Reenvía un SMS recibido como correo."""
    cfg = load_env()
    to_addr = (
        (mail_to or "").strip()
        or cfg.get("SMS_MAIL_TO", "").strip()
        or os.environ.get("SMS_MAIL_TO", "").strip()
        or cfg.get("MAIL_TO", "").strip()
    )
    if not to_addr:
        raise RuntimeError("SMS_MAIL_TO / MAIL_TO no configurados")
    subject = f"[Zeromatic SMS] de {sender or 'desconocido'}"
    body = (
        "SMS recibido en Zeromatic.\n\n"
        f"Remitente: {sender or 'desconocido'}\n"
        f"Fecha módem: {when or '—'}\n\n"
        "--- Mensaje ---\n"
        f"{text}\n"
    )
    send_gmail(subject, body, mail_to=to_addr, cfg=cfg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default="")
    ap.add_argument("--transcript", default="")
    ap.add_argument("--audio", default="")
    ap.add_argument("--sms-from", default="", help="Reenviar SMS: remitente")
    ap.add_argument("--sms-time", default="", help="Marca de tiempo del SMS")
    ap.add_argument("--sms-text", default="", help="Cuerpo del SMS")
    ap.add_argument("--sms-text-file", default="", help="Fichero con el cuerpo del SMS")
    ap.add_argument("--mail-to", default="", help="Destino (override)")
    args = ap.parse_args()

    try:
        if args.sms_from or args.sms_text or args.sms_text_file:
            text = args.sms_text
            if args.sms_text_file:
                text = Path(args.sms_text_file).read_text(encoding="utf-8", errors="replace")
            send_sms_forward(args.sms_from, args.sms_time, text, mail_to=args.mail_to or None)
            return 0

        if not args.meta or not args.transcript:
            print("Faltan --meta/--transcript (o modo --sms-from)", file=sys.stderr)
            return 2

        cfg = load_env()
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
        called_raw = meta_field("called-id", "")
        if called_raw.lower() in {"", "no configurado", "desconocido", "(vacío)", "(vacio)"}:
            called_raw = ""
        called = (
            called_raw
            or cfg.get("LINE_NUMBER", "").strip()
            or cfg.get("CALLED_ID", "").strip()
            or "no configurado"
        )

        attachments: list[Path] = []
        audio = Path(args.audio) if args.audio else None
        if audio and audio.is_file():
            attachments.append(audio)

        send_gmail(
            f"[Zeromatic] Mensaje de {caller}",
            "Nuevo mensaje en Zeromatic.\n\n"
            f"Quién llama (Caller-ID): {caller}\n"
            f"Línea contestada (Called-ID): {called}\n\n"
            f"{meta}\n"
            "--- Transcripción ---\n"
            f"{transcript}\n",
            mail_to=args.mail_to or None,
            attachments=attachments,
            cfg=cfg,
        )
        return 0
    except Exception as e:
        print(f"ERROR correo: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
