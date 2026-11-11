#!/usr/bin/env python3
"""Desbloquea la SIM (PIN) y opcionalmente desactiva el PIN en un SIM800.

Uso:
  SIM_PIN=4912 /opt/zeromatic/venv/bin/python3 bin/gsm_setup_sim.py
  /opt/zeromatic/venv/bin/python3 bin/gsm_setup_sim.py --pin 4912 --keep-pin
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import serial

# Permitir import local
sys.path.insert(0, str(Path(__file__).resolve().parent))
from modem_gsm import GsmModem  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Configura PIN de la SIM en SIM800")
    p.add_argument("--device", default=os.environ.get("MODEM_DEVICE", "/dev/ttyUSB0"))
    p.add_argument("--baud", type=int, default=int(os.environ.get("MODEM_BAUD", "115200")))
    p.add_argument("--pin", default=os.environ.get("SIM_PIN", "4912"))
    p.add_argument(
        "--keep-pin",
        action="store_true",
        help="Solo desbloquea; no desactiva la petición de PIN",
    )
    p.add_argument(
        "--wait-sim",
        type=float,
        default=90.0,
        help="Segundos máximos esperando que aparezca la SIM",
    )
    args = p.parse_args()

    if not Path(args.device).exists():
        print(f"ERROR: no existe {args.device}", file=sys.stderr)
        return 2

    os.environ["SIM_PIN"] = args.pin
    os.environ["DISABLE_SIM_PIN"] = "0" if args.keep_pin else "1"

    print(f"Abriendo {args.device} @ {args.baud}…")
    m = GsmModem(args.device, args.baud)
    try:
        if not m.probe_at():
            print("ERROR: sin respuesta AT", file=sys.stderr)
            return 3
        m.cmd("ATE0", expect_ok=False)
        m.cmd("AT+CMEE=2", expect_ok=False)
        print(f"Esperando SIM hasta {args.wait_sim:.0f}s…")
        end = time.time() + args.wait_sim
        status = "TIMEOUT"
        while time.time() < end:
            status = m._wait_cpin(5.0)
            if status in {"READY", "SIM PIN", "SIM PUK"}:
                break
            print(f"  aún no: {status}")
        if status not in {"READY", "SIM PIN"}:
            print(
                f"ERROR: SIM no usable ({status}). "
                "Asienta la tarjeta en la bandeja del SIM800 y reintenta.",
                file=sys.stderr,
            )
            return 4
        if status == "READY" and args.keep_pin:
            print("SIM ya READY (PIN no pedido ahora).")
        else:
            m.unlock_and_optional_disable_pin(args.pin)
        m.wait_network(60.0)
        print("OK — SIM lista.")
        return 0
    finally:
        m.close()


if __name__ == "__main__":
    raise SystemExit(main())
