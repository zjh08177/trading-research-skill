"""Shared bootstrap for Unusual Whales vendor CLIs (read-only market data).

Loads the API key from the SSOT ``~/.config/tradingagents/unusualwhales.env``
(override path via ``UNUSUALWHALES_ENV``); never hardcode or echo the key.
Requests carry a browser-ish User-Agent — the default python-requests UA is
blocked by Cloudflare (error 1010). ``get_json`` is the sole network seam
(monkeypatched in tests); ``data_or_die`` maps UW statuses onto the uniform
vendor exit codes: 0 ok / 2 config-auth / 3 no-data-or-tier-gate / 4
rate-limit / 1 other. The tier gate matters: several endpoints (darkpool)
serve only a rolling 90-trading-day window and answer 403
``historic_data_access_missing`` beyond it — that is a data-window fact, not
an auth failure, so it exits 3 with the server's boundary message.
"""
import os
import sys
import time
from pathlib import Path

# scripts/ on path so engine.cli resolves under direct CLI invocation too
# (sys.path[0] is scripts/vendors/ when a vendor CLI runs as __main__).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from engine.cli import die, fact, write_atomic  # noqa: F401  (plumbing SSOT)
from engine.cli import emit as _cli_emit

BASE = "https://api.unusualwhales.com"
CREDS_PATH = Path(os.environ.get(
    "UNUSUALWHALES_ENV",
    str(Path.home() / ".config" / "tradingagents" / "unusualwhales.env"),
))
UA = "tradingagents-quant/1.0 (+python-requests)"
RETRIES_429 = 3


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


def emit(obj):
    # insertion-order stdout is this vendor family's historical byte contract
    _cli_emit(obj, sort_keys=False)


def api_key():
    k = os.environ.get("UNUSUAL_WHALES_API_KEY")
    if not k:
        die("UNUSUAL_WHALES_API_KEY missing (expected in %s)" % CREDS_PATH, 2)
    return k


_session = None


def get_json(path, params=None):
    """Sole network seam. Returns (status_code, parsed_json_or_text).
    Transient transport failures (connection reset mid-pull on long paged
    fetches) retry with backoff before dying — a multi-hundred-call fetch
    must not be killed by one dropped socket."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Authorization": "Bearer %s" % api_key(),
            "Accept": "application/json, text/plain",
            "User-Agent": UA,
        })
    last = None
    for attempt in range(4):
        try:
            r = _session.get(BASE + path, params=params or {}, timeout=30)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, r.text[:300]
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(2.0 * (attempt + 1))
    die("UW transport failure after retries on %s: %s" % (path, last), 1)


def data_or_die(path, params=None):
    """GET with 429 retry; return the ``data`` array (or the raw body if the
    response is already a list). Non-200s map to the uniform exit codes."""
    for attempt in range(RETRIES_429 + 1):
        status, body = get_json(path, params)
        if status != 429:
            break
        if attempt < RETRIES_429:
            time.sleep(2.0 * (attempt + 1))
    if status == 429:
        die("UW rate limited (429) after %d retries: %s" % (RETRIES_429, path), 4)
    if status in (401,):
        die("UW auth failed (401): check key in %s" % CREDS_PATH, 2)
    if status == 403:
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("message", "") if isinstance(body, dict) else str(body)
        if code == "historic_data_access_missing":
            die("UW tier gate: %s" % msg.split("\n")[0], 3)
        die("UW forbidden (403): %s" % str(body)[:200], 2)
    if status == 404:
        die("UW no data (404): %s" % path, 3)
    if status != 200:
        die("UW HTTP %s on %s: %s" % (status, path, str(body)[:200]), 1)
    data = body.get("data") if isinstance(body, dict) else body
    if data is None:
        die("UW malformed response (no data field) on %s" % path, 1)
    return data
