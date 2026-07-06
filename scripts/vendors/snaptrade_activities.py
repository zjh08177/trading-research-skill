"""SnapTrade broker transaction history CLI (read-only, self-contained).

Streams normalized historical activities (buys/sells/dividends/fees/transfers/
option events) across ALL SnapTrade-linked accounts as JSONL on stdout — one row
per line — for the portfolio-history activities store
(reports/portfolio/activities/). Reverse-chronological by trade_date; the SDK
pages at ≤1000/request, so this walks offset += limit until a short page,
cross-checked against pagination.total.

SELF-CONTAINED bootstrap (no engine.cli / no _snaptrade_common import): the
skill-repo vendors dir has neither on path when run under the quant-engine venv
that carries the SnapTrade SDK. Creds load from the shared SSOT
``~/.config/tradingagents/snaptrade.env`` (SNAPTRADE_ENV overrides). Read-only:
lists accounts + activities only, no order/trade endpoint. Every error is routed
through a scrubber so userSecret / consumerKey / signature never print.

Exit: 0 ok · 2 config/auth · 3 no-data/bad-request · 4 rate-limit · 1 other.

Usage: snaptrade_activities.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                               [--account ID] [--type TYPE]
Run under the quant-engine venv:
  <workspace>/quant-engine-skill/.venv/bin/python snaptrade_activities.py ...
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

LIMIT = 1000
SRC = "snaptrade"
CREDS_PATH = Path(os.environ.get(
    "SNAPTRADE_ENV", str(Path.home() / ".config" / "tradingagents" / "snaptrade.env")))

# Redact secrets an SDK exception/dump might echo (signed API, not bearer tokens).
_SECRET_RE = re.compile(
    r'((?:userSecret|consumerKey|signature)["\']?\s*[:=]\s*["\']?)[^"\'&\s,}]+', re.I)


def _scrub(s):
    return _SECRET_RE.sub(r"\1***", str(s))


def die(msg, code):
    sys.stderr.write("ERROR: %s\n" % _scrub(msg))
    raise SystemExit(code)


def _load_env():
    """Minimal KEY=VALUE loader; never overrides an already-set env var."""
    if not CREDS_PATH.exists():
        return
    for line in CREDS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _creds():
    cid = os.environ.get("SNAPTRADE_CLIENT_ID")
    ck = os.environ.get("SNAPTRADE_CONSUMER_KEY")
    uid = os.environ.get("SNAPTRADE_USER_ID")
    us = os.environ.get("SNAPTRADE_USER_SECRET")
    if not cid or not ck:
        die("SnapTrade partner creds missing (SNAPTRADE_CLIENT_ID/CONSUMER_KEY in %s)"
            % CREDS_PATH, 2)
    if not uid or not us:
        die("SnapTrade user not registered (SNAPTRADE_USER_ID/USER_SECRET in %s)"
            % CREDS_PATH, 2)
    return cid, ck, uid, us


def _client(cid, ck):
    from snaptrade_client import SnapTrade
    return SnapTrade(consumer_key=ck, client_id=cid)


def plain(x):
    """Convert an SDK Schema body into plain json-able Python."""
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, str):
        return str(x)
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    if isinstance(x, Mapping):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, Sequence):
        return [plain(v) for v in x]
    try:
        f = float(x)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return str(x)


def _api_exc():
    """The SDK's ApiException, or an empty tuple when the SDK is absent (tests run
    on a bare interpreter) so `except _api_exc()` simply catches nothing there."""
    try:
        from snaptrade_client.exceptions import ApiException
        return ApiException
    except Exception:  # pragma: no cover
        return ()


def die_from_exc(e):
    """Map an SDK exception to the uniform vendor exit codes (never returns)."""
    ApiException = _api_exc()
    status = getattr(e, "status", None)
    msg = _scrub(e)
    if ApiException and isinstance(e, ApiException):
        if status in (401, 403):
            die("SnapTrade auth/authorization failed (%s): %s" % (status, msg), 2)
        if status == 429:
            die("SnapTrade rate limited (429): %s" % msg, 4)
        if status in (400, 404, 410):
            die("SnapTrade no-data/bad-request (%s): %s" % (status, msg), 3)
        die("SnapTrade API error (%s): %s" % (status, msg), 1)
    die("%s: %s" % (type(e).__name__, msg), 1)


def _sym(row):
    """Underlying ticker for the row: equity raw_symbol, else the option symbol."""
    s = row.get("symbol")
    if isinstance(s, dict):
        return s.get("raw_symbol") or s.get("symbol")
    opt = row.get("option_symbol")
    if isinstance(opt, dict):
        return opt.get("ticker") or opt.get("raw_symbol") or opt.get("symbol")
    return None


def normalize(row, account_id, broker):
    """One SDK activity → the pinned flat schema (§4). Pure."""
    cur = row.get("currency")
    return {
        "trade_date": row.get("trade_date"),
        "settlement_date": row.get("settlement_date"),
        "account_id": account_id,
        "broker": broker or row.get("institution"),
        "symbol": _sym(row),
        "type": row.get("type"),
        "units": row.get("units"),
        "price": row.get("price"),
        "amount": row.get("amount"),
        "currency": cur.get("code") if isinstance(cur, dict) else cur,
        "description": row.get("description"),
    }


def _fetch_page(client, uid, us, aid, start, end, offset):
    """One page, retried up to 3x with backoff on transient connection flaps
    (api.snaptrade.com throttles bursts). Genuine API errors propagate."""
    import time
    ApiException = _api_exc()
    for attempt in range(3):
        try:
            resp = client.account_information.get_account_activities(
                account_id=aid, user_id=uid, user_secret=us,
                start_date=start, end_date=end, offset=offset, limit=LIMIT)
            return plain(resp.body) or {}
        except ApiException:
            raise                                    # real API status → caller maps it
        except Exception:                            # noqa: BLE001 — transient transport
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    return {}


def fetch_account(client, uid, us, acct, start, end):
    """All activities for one account (offset/limit loop, total cross-check).
    Returns normalized rows."""
    aid = acct.get("id")
    broker = acct.get("institution_name") or acct.get("brokerage")
    rows, offset, total = [], 0, None
    while True:
        body = _fetch_page(client, uid, us, aid, start, end, offset)
        data = body.get("data") or []
        total = (body.get("pagination") or {}).get("total", total)
        rows.extend(normalize(r, aid, broker) for r in data)
        if len(data) < LIMIT:
            break
        offset += LIMIT
    if total is not None and len(rows) != total:     # surface a pagination mismatch, don't hide it
        sys.stderr.write("WARN: account %s returned %d rows but pagination.total=%s\n"
                         % (aid, len(rows), total))
    return rows


def _parse_date(s, flag):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        die("invalid %s %r (expected YYYY-MM-DD)" % (flag, s), 2)


def main(argv=None):
    p = argparse.ArgumentParser(prog="snaptrade_activities")
    p.add_argument("--start", default="2000-01-01")
    p.add_argument("--end", default=datetime.date.today().isoformat())
    p.add_argument("--account", default=None, help="limit to one SnapTrade account id")
    p.add_argument("--type", default=None, help="reserved; SDK type filter")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    start, end = _parse_date(args.start, "--start"), _parse_date(args.end, "--end")

    _load_env()
    cid, ck, uid, us = _creds()
    try:
        client = _client(cid, ck)
        accts = plain(client.account_information.list_user_accounts(
            user_id=uid, user_secret=us).body) or []
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        die_from_exc(e)
    if not accts:
        die("no linked SnapTrade brokerage accounts to inspect", 3)
    if args.account:
        accts = [a for a in accts if a.get("id") == args.account]
        if not accts:
            die("account %r not found among linked accounts" % args.account, 3)

    out = sys.stdout
    n_rows, skipped = 0, 0
    for a in accts:
        try:
            rows = fetch_account(client, uid, us, a, start, end)
        except SystemExit:
            raise                                    # die() from a mapped API status
        except Exception as e:  # noqa: BLE001 — transient per-account failure: skip, keep going
            skipped += 1
            sys.stderr.write("WARN: skipped account %s (%s)\n"
                             % (a.get("id"), _scrub(type(e).__name__)))
            continue
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_rows += 1
    sys.stderr.write("activities: %d rows from %d/%d accounts (%d skipped) %s..%s\n"
                     % (n_rows, len(accts) - skipped, len(accts), skipped, start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
