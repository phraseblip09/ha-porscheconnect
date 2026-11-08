#!/usr/bin/env python3
"""Quick health check for the vendored Porsche Connect library - no Home Assistant needed.

Runs login + vehicle list + stored overview against the real Porsche API and prints
what came back. Takes ~10 s. Use it to tell within a minute whether a breakage is
on Porsche's side (login / endpoints) or inside Home Assistant.

Usage:
    python3 tools/check.py                      # asks for e-mail and password
    python3 tools/check.py --token token.json   # reuse a saved token (no login)
    python3 tools/check.py --debug              # full HTTP debug log

A successful login saves the token to token.json (git-ignored) so that further
runs don't cost an Auth0 login - every login counts towards a captcha lockout.

Requires: pip3 install httpx beautifulsoup4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from getpass import getpass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "porscheconnect"))

from pyporscheconnectapi.account import PorscheConnectAccount  # noqa: E402
from pyporscheconnectapi.connection import Connection  # noqa: E402
from pyporscheconnectapi.exceptions import (  # noqa: E402
    PorscheCaptchaRequiredError,
    PorscheExceptionError,
    PorscheWrongCredentialsError,
)

TOKEN_FILE = REPO_ROOT / "tools" / "token.json"


def _fmt(value, unit=""):
    return f"{value}{unit}" if value is not None else "–"


async def run(email: str | None, password: str | None, token: dict | None, captcha: str | None, state: str | None, verifier: str | None) -> int:
    conn = Connection(
        email=email,
        password=password,
        captcha_code=captcha,
        state=state,
        code_verifier=verifier,
        token=token or {},
    )
    try:
        print("1/3 Token …", end=" ", flush=True)
        tok = await conn.get_token()
        print("OK" if tok.access_token else "MISSING")
        TOKEN_FILE.write_text(json.dumps(dict(tok), indent=2))
        print(f"    Token saved: {TOKEN_FILE}")

        print("2/3 Vehicle list …", end=" ", flush=True)
        account = PorscheConnectAccount(connection=conn)
        vehicles = await account.get_vehicles()
        print(f"OK ({len(vehicles)} vehicle(s))")

        print("3/3 Status …")
        for v in vehicles:
            await v.get_stored_overview()
            print(f"    {v.data.get('modelName', '?')}  VIN {v.vin}")
            charging = v.data.get("CHARGING_SUMMARY", {})
            print(f"      Battery: {_fmt(v.main_battery_level, ' %')}   Range: {_fmt(v.electric_range, ' km')}   Mileage: {_fmt(v.mileage, ' km')}")
            print(f"      Charging: {_fmt(charging.get('status'))}  Mode: {_fmt(charging.get('mode'))}  Locked: {v.vehicle_locked}  Data from: {_fmt(v.last_global_update)}")
        print("\nALL OK – Porsche API and library are working.")
        return 0

    except PorscheCaptchaRequiredError as exc:
        print("\nCAPTCHA REQUIRED – Porsche locked the account after too many logins.")
        print("   In Home Assistant: integration → Re-authenticate, enter the captcha.")
        print(f"   Captcha image (data URL, {len(str(exc.captcha))} chars): {str(exc.captcha)[:80]}…")
        print(f"   state={exc.state}\n   code_verifier={exc.code_verifier}")
        return 2
    except PorscheWrongCredentialsError:
        print("\nLOGIN REJECTED – wrong e-mail/password?")
        return 3
    except PorscheExceptionError as exc:
        print(f"\nPORSCHE ERROR: {exc.message or exc}")
        if exc.request_url:
            print(f"   URL: {exc.request_url}")
        if exc.response_body:
            print(f"   Response: {exc.response_body[:300]}")
        return 4
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--token", type=Path, help="JSON token file (default: tools/token.json if present)")
    parser.add_argument("--captcha", help="captcha code (with --state and --verifier from a previous run)")
    parser.add_argument("--state")
    parser.add_argument("--verifier")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    token = None
    token_path = args.token or (TOKEN_FILE if TOKEN_FILE.exists() else None)
    if token_path and token_path.exists():
        token = json.loads(token_path.read_text())
        print(f"Token loaded from {token_path}")

    email = args.email
    password = args.password
    if token is None or args.captcha:
        email = email or input("Porsche Connect E-Mail: ")
        password = password or getpass("Password: ")

    return asyncio.run(run(email, password, token, args.captcha, args.state, args.verifier))


if __name__ == "__main__":
    sys.exit(main())
