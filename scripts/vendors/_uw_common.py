"""Self-contained Unusual Whales transport for the trading-research skill.

Deliberately standalone: it does NOT import the quant-engine-skill SSOT (which
now couples to that skill's ``engine.cli`` package) nor this skill's ``_common``
(which drags in dotenv + the upstream path). It carries its own tiny plumbing so
the P8 options CLI has one dependency-light seam.

Loads the API key from ``~/.config/tradingagents/unusualwhales.env`` (override
via ``UNUSUALWHALES_ENV``); never hardcode or echo the key. Requests carry a
browser-ish User-Agent — the default python-requests UA is blocked by Cloudflare
(1010). ``get_json`` is the sole network seam (monkeypatched in tests).
``data_or_die`` maps UW statuses to the uniform vendor exit codes
(0 ok / 2 config-auth / 3 no-data-or-tier-gate / 4 rate-limit / 1 other); the
P8 CLI uses ``get_json`` directly for per-endpoint fail-loud instead.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = "https://api.unusualwhales.com"
CREDS_PATH = Path(os.environ.get(
    "UNUSUALWHALES_ENV",
    str(Path.home() / ".config" / "tradingagents" / "unusualwhales.env"),
))
UA = "tradingagents-research/1.0 (+python-requests)"
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


def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def emit(obj):
    print(json.dumps(obj, separators=(",", ":")))


def die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.replace(tmp, path)


def api_key():
    k = os.environ.get("UNUSUAL_WHALES_API_KEY")
    if not k:
        die("UNUSUAL_WHALES_API_KEY missing (expected in %s)" % CREDS_PATH, 2)
    return k


_session = None


def get_json(path, params=None):
    """Sole network seam. Returns (status_code, parsed_json_or_text). Transient
    transport failures retry with backoff before dying — a multi-endpoint fetch
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
    """GET with 429 retry; return the ``data`` array (or the raw body). Non-200s
    map to the uniform exit codes. PROCESS-FATAL — single-endpoint CLIs only;
    uw_options uses get_json per endpoint for non-fatal partial failure."""
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
