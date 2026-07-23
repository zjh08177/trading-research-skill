"""Interactive Schwab OAuth re-mint: fills the "manual OAuth flow" gap noted in
``schwab_auth.py`` — that module can refresh an access token from a live refresh
token, but nothing in this repo has ever minted the *first* refresh token via the
authorization-code exchange. This does exactly that, once, by hand.

Requires a human in the loop: Schwab's login is MFA-protected and this script
never sees your Schwab password. You open the printed URL yourself, log in in
your own browser, and paste back the URL Schwab redirects you to afterward.
"""
import argparse
import os
from urllib.parse import parse_qs, urlencode, urlparse

from datetime import datetime, timedelta, timezone

from _common import CREDS_PATH, die

import requests

from tradingagents.dataflows.schwab_auth import TokenStore, resolve_token_path, save_store

AUTHORIZE_ENDPOINT = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"
REQUEST_TIMEOUT = 15
DEFAULT_REDIRECT_URI = "https://127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--redirect-uri",
        default=DEFAULT_REDIRECT_URI,
        help=(
            "Must exactly match the callback URL registered for this app in the "
            f"Schwab Developer Portal (default {DEFAULT_REDIRECT_URI!r})."
        ),
    )
    ap.add_argument("--token-path", default=None, help="Override the token store path.")
    args = ap.parse_args()

    client_id = os.getenv("SCHWAB_CLIENT_ID")
    client_secret = os.getenv("SCHWAB_CLIENT_SECRET")
    if not (client_id and client_secret):
        die(
            f"SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET not set (loaded from {CREDS_PATH}).",
            2,
        )

    authorize_url = f"{AUTHORIZE_ENDPOINT}?{urlencode({'client_id': client_id, 'redirect_uri': args.redirect_uri})}"
    print("1. Open this URL in your own browser and log in to Schwab:\n")
    print(f"   {authorize_url}\n")
    print("2. After you approve access, Schwab redirects to a URL starting with")
    print(f"   {args.redirect_uri} — the page will likely show an error/blank page,")
    print("   that's expected. Copy the FULL resulting URL from the address bar.\n")

    pasted = input("3. Paste that full URL here: ").strip()
    query = parse_qs(urlparse(pasted).query)
    codes = query.get("code")
    if not codes:
        die(f"No ?code=... found in the pasted URL: {pasted!r}", 2)
    code = codes[0]

    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": args.redirect_uri,
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        die(f"Token exchange failed ({resp.status_code}): {resp.text}", 1)

    body = resp.json()
    now = datetime.now(timezone.utc)
    expires_in = body.get("expires_in") or 1800
    store = TokenStore(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        access_expires_at=now + timedelta(seconds=expires_in),
        refresh_issued_at=now,
    )
    path = resolve_token_path(args.token_path)
    save_store(path, store)
    print(f"\nWrote fresh token store to {path} (refresh token valid ~7 days from now).")


if __name__ == "__main__":
    main()
