"""Shared bootstrap for SnapTrade vendor CLIs (venv-agnostic, no upstream coupling).

Loads creds from the shared SSOT ``~/.config/tradingagents/snaptrade.env`` (both
v2 and v3 read the same file), builds the SnapTrade client, and provides the
vendor fact/emit/die contract used by the rest of the CLI layer. SnapTrade is the
cross-broker position source: unlike the Schwab-only path it aggregates every
brokerage the owner has linked (Robinhood, Schwab, ...), closing the Schwab-only
gap. Reads only — the register/link POSTs live in ``snaptrade_setup.py``, never
here. Exit codes match the rest of the layer: 0 ok / 2 config-auth / 3 no-data /
4 rate-limit / 1 other; error paths are stdout-silent.
"""
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# Sensitive tokens that may be echoed inside SDK error/request dumps. SnapTrade's
# signed API carries userSecret/consumerKey/signature (not bearer tokens).
_SECRET_RE = re.compile(
    r'((?:userSecret|consumerKey|signature)["\']?\s*[:=]\s*["\']?)'
    r'[^"\'&\s,}]+', re.I)


def _scrub(s):
    """Redact secrets that an SDK exception might carry (never trust its text)."""
    return _SECRET_RE.sub(r"\1***", str(s))

CREDS_PATH = Path(os.environ.get(
    "SNAPTRADE_ENV",
    str(Path.home() / ".config" / "tradingagents" / "snaptrade.env"),
))


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


_load_env()


# ---- vendor fact/emit/die contract (self-contained; no _common.py dependency) ----

def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def emit(facts):
    print(json.dumps(facts, separators=(",", ":")))


def die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ---- creds + client ----

def partner_creds():
    cid = os.environ.get("SNAPTRADE_CLIENT_ID")
    ck = os.environ.get("SNAPTRADE_CONSUMER_KEY")
    if not cid or not ck:
        die("SnapTrade partner creds missing (SNAPTRADE_CLIENT_ID/CONSUMER_KEY "
            "in %s)" % CREDS_PATH, 2)
    return cid, ck


def user_creds():
    uid = os.environ.get("SNAPTRADE_USER_ID")
    us = os.environ.get("SNAPTRADE_USER_SECRET")
    if not uid or not us:
        die("SnapTrade user not registered (run snaptrade_setup.py register); "
            "missing SNAPTRADE_USER_ID/USER_SECRET in %s" % CREDS_PATH, 2)
    return uid, us


def client():
    from snaptrade_client import SnapTrade
    cid, ck = partner_creds()
    return SnapTrade(consumer_key=ck, client_id=cid)


def save_user_creds(user_id, user_secret):
    """Persist the minted (userId, userSecret) into the SSOT, 0600."""
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = {}
    order = []
    if CREDS_PATH.exists():
        for raw in CREDS_PATH.read_text().splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                k, v = raw.split("=", 1)
                if k.strip() not in lines:
                    order.append(k.strip())
                lines[k.strip()] = v.strip()
    for k in ("SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"):
        if k not in lines:
            order.append(k)
    lines["SNAPTRADE_USER_ID"] = user_id
    lines["SNAPTRADE_USER_SECRET"] = user_secret
    header = ("# SnapTrade creds SSOT — shared by v2 (trading-research) + "
              "v3 (quant-engine) vendor CLIs.\n")
    body = header + "".join("%s=%s\n" % (k, lines[k]) for k in order)
    # Create with 0600 atomically so the secret is never briefly world-readable
    # (a plain write-then-chmod leaves a window at the umask default).
    fd = os.open(str(CREDS_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.chmod(CREDS_PATH, 0o600)  # tighten perms on a pre-existing file too


# ---- response helpers ----

def plain(x):
    """Convert an SDK Schema body into plain json-able Python (dict/list/scalars)."""
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
    try:  # numbers incl. Decimal / konfig numeric subclasses
        f = float(x)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return str(x)


def die_from_exc(e):
    """Map an SDK exception to the uniform vendor exit codes (never returns)."""
    try:
        from snaptrade_client.exceptions import ApiException
    except Exception:  # pragma: no cover - SDK always importable at call time
        ApiException = ()
    status = getattr(e, "status", None)
    msg = _scrub(e)  # SDK errors can echo the userSecret / signature — redact
    if ApiException and isinstance(e, ApiException):
        if status in (401, 403):
            die("SnapTrade auth/authorization failed (%s): %s" % (status, msg), 2)
        if status == 429:
            die("SnapTrade rate limited (429): %s" % msg, 4)
        if status in (400, 404, 410):
            die("SnapTrade no-data/bad-request (%s): %s" % (status, msg), 3)
        die("SnapTrade API error (%s): %s" % (status, msg), 1)
    die("%s: %s" % (type(e).__name__, msg), 1)
