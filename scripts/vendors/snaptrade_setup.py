"""SnapTrade one-time setup CLI (owner ops, NOT part of the research pipeline).

This is the only SnapTrade module that issues POSTs (register a user, mint a
connection-portal URL). It is deliberately separate from the read-only
``snaptrade_account.py`` / ``snaptrade_holdings.py`` so the read CLIs carry no
mutation path. None of these operations place trades or move money — SnapTrade's
data scope is read-only; the "connect" portal only authorizes SnapTrade to READ
the owner's brokerage.

Subcommands:
  register [--force]   Mint the SnapTrade user (userId+userSecret) → creds SSOT.
                       --force deletes an existing user first (irreversible) and
                       re-registers to recover a lost secret.
  link [--broker X]    Print the Connection Portal URL the owner opens once in a
                       browser to link a brokerage (optionally deep-linked to X,
                       e.g. ROBINHOOD). The URL is single-use and expires ~5 min.
  accounts             List the brokerage accounts SnapTrade can currently read.

Exit codes: 0 ok / 2 config-auth / 3 no-data / 4 rate-limit / 1 other.
"""
import argparse
import json
import os
import sys

from _snaptrade_common import (
    client,
    die,
    die_from_exc,
    partner_creds,
    plain,
    save_user_creds,
    user_creds,
)

DEFAULT_USER_ID = os.environ.get("SNAPTRADE_USER_ID") or "eric-tradingagents"


def _personal_key_recover(c, existing_uid, err):
    """Personal keys (PERS-) auto-provision one user and disable registerUser.
    Recover by finding that user and rotating its secret (rotation keeps broker
    connections). ``err`` is the registerUser failure that sent us here."""
    try:
        users = plain(c.authentication.list_snap_trade_users().body) or []
    except Exception:  # noqa: BLE001
        die_from_exc(err)
    if not users:
        die_from_exc(err)
    uid = existing_uid if (existing_uid and existing_uid in users) else users[0]
    try:
        r = c.authentication.reset_snap_trade_user_secret(user_id=uid)
    except Exception as e:  # noqa: BLE001
        die_from_exc(e)
    b = plain(r.body)
    return b.get("userId", uid), b.get("userSecret")


def _do_register(force):
    partner_creds()  # fail fast (exit 2) if partner creds absent
    existing_secret = os.environ.get("SNAPTRADE_USER_SECRET")
    existing_uid = os.environ.get("SNAPTRADE_USER_ID")
    c = client()
    # Idempotent: if stored creds already read accounts, keep them.
    if existing_secret and existing_uid and not force:
        try:
            c.account_information.list_user_accounts(
                user_id=existing_uid, user_secret=existing_secret)
            print(json.dumps({"status": "already-registered",
                              "userId": existing_uid}, indent=2))
            return 0
        except Exception:  # noqa: BLE001 — stale secret, fall through to re-mint
            pass
    # Partner keys: registerUser mints a fresh user+secret. Personal keys reject
    # registerUser (code 1012) — recover the auto-provisioned user + rotate.
    try:
        resp = c.authentication.register_snap_trade_user(user_id=DEFAULT_USER_ID)
        body = plain(resp.body)
        user_id, user_secret = body.get("userId"), body.get("userSecret")
    except Exception as e:  # noqa: BLE001
        user_id, user_secret = _personal_key_recover(c, existing_uid, e)
    if not user_secret:
        die("no userSecret obtained (register + personal-key recovery both "
            "yielded none)", 1)
    save_user_creds(user_id, user_secret)
    print(json.dumps({"status": "registered", "userId": user_id,
                      "userSecret": "***saved to creds SSOT***"}, indent=2))
    return 0


def _do_link(broker):
    uid, us = user_creds()
    kwargs = {"user_id": uid, "user_secret": us}
    if broker:
        kwargs["broker"] = broker
    try:
        resp = client().authentication.login_snap_trade_user(**kwargs)
    except Exception as e:  # noqa: BLE001
        die_from_exc(e)
    body = plain(resp.body)
    url = body.get("redirectURI") if isinstance(body, dict) else None
    if not url:
        die("login returned no redirectURI: %s" % body, 1)
    print(json.dumps({"connect_url": url, "broker": broker or "any",
                      "note": "single-use, expires ~5 min; open in a browser"},
                     indent=2))
    return 0


def _do_accounts():
    uid, us = user_creds()
    try:
        resp = client().account_information.list_user_accounts(
            user_id=uid, user_secret=us)
    except Exception as e:  # noqa: BLE001
        die_from_exc(e)
    accounts = plain(resp.body) or []
    slim = []
    for a in accounts:
        bal = (a.get("balance") or {}).get("total") or {}
        slim.append({
            "id": a.get("id"),
            "institution": a.get("institution_name") or a.get("brokerage"),
            "name": a.get("name"),
            "number": a.get("number"),
            "total_value": bal.get("amount"),
            "currency": bal.get("currency"),
        })
    print(json.dumps({"n_accounts": len(slim), "accounts": slim}, indent=2))
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="snaptrade_setup")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("register")
    pr.add_argument("--force", action="store_true")
    pl = sub.add_parser("link")
    pl.add_argument("--broker", default=None)
    sub.add_parser("accounts")
    args = p.parse_args(argv)
    if args.cmd == "register":
        return _do_register(args.force)
    if args.cmd == "link":
        return _do_link(args.broker)
    if args.cmd == "accounts":
        return _do_accounts()
    p.error("unknown command")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
