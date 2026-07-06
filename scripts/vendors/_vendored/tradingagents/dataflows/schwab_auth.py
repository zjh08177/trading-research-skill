"""Schwab OAuth refresh + 7-day cadence wrapper (Tier-B unattended-loop prereq).

W3 ``schwab.precheck_oauth`` only catches a *missing* access token. An unattended
live loop needs two more things, provided here:

  * **auto-refresh** — mint a fresh 30-minute access token from the refresh token
    and persist it, so the next 30-minute window reuses it instead of failing;
  * **cadence guard** — the refresh token itself lives only 7 days from issuance
    (Schwab does NOT extend it on an access refresh), so fail loud at run start
    when it is expired or near expiry rather than dying mid-loop at hour 100.

Persistence is a small JSON token store (``access_token``, ``refresh_token``,
``access_expires_at``, ``refresh_issued_at``) written ``0600`` — a refresh token
is a long-lived secret.

LIVE-UNTESTABLE until Schwab P1 (no OAuth app yet): the live token exchange is the
mockable seam ``_token_request`` and the clock is injected (``now=``), mirroring
``schwab.SchwabEquityVendor``'s ``_request`` seam. Live verification defers to EC10.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from .errors import SchwabReauthRequiredError, VendorNotConfiguredError

# Schwab OAuth lifetimes (seconds): access 30 min, refresh 7 days from issuance.
ACCESS_TTL = 30 * 60
REFRESH_TTL = 7 * 24 * 60 * 60
# Re-auth warning band: fail loud once the refresh token has <24h of life left.
REAUTH_WARN = 24 * 60 * 60
# Treat an access token as expired this many seconds early (clock skew / call latency).
CLOCK_SKEW = 60

TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"
REQUEST_TIMEOUT = 15
# Default store path. NOTE: only this basename is gitignored — any custom path set
# via SCHWAB_TOKEN_PATH / config["schwab_token_path"] must live outside the work tree.
DEFAULT_TOKEN_PATH = ".schwab_token.json"


def resolve_token_path(token_path: str | None = None) -> str:
    """Single source of the token-store path: explicit arg > env > default.

    Resolved at call time (not import) so precheck and the data-fetch credential
    ALWAYS resolve to the same file. The path is intentionally NOT read from the run
    config: the module-level fetch seam (``schwab._request``) has no config access,
    so a config-only path would let precheck validate a different store than the
    fetch reads (re-opening a false green). Set ``SCHWAB_TOKEN_PATH`` to override.
    """
    return token_path or os.getenv("SCHWAB_TOKEN_PATH") or DEFAULT_TOKEN_PATH


def _as_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive ISO datetime to tz-aware UTC.

    A store hand-written by the manual OAuth bootstrap may carry naive timestamps
    (``2026-06-28T12:00:00``, no offset). Without this, every comparison against a
    tz-aware ``now`` raises a bare ``TypeError`` — which is NOT a ``VendorError``,
    so it bypasses the fail-loud taxonomy and dies opaque instead of "re-auth".
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@dataclass
class TokenStore:
    """The persisted Schwab OAuth state. Datetimes are tz-aware UTC.

    The two token fields are ``repr=False`` so the auto-generated repr/str never
    prints the secrets into logs, tracebacks, or error-tracker local-variable dumps.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: datetime
    refresh_issued_at: datetime

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at.isoformat(),
            "refresh_issued_at": self.refresh_issued_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TokenStore:
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            access_expires_at=_as_utc(datetime.fromisoformat(d["access_expires_at"])),
            refresh_issued_at=_as_utc(datetime.fromisoformat(d["refresh_issued_at"])),
        )


def load_store(path: str) -> TokenStore:
    """Read the token store; raise ``VendorNotConfiguredError`` if it is absent.

    Absence means the one-time manual OAuth flow has not produced credentials yet
    — the same "vendor unavailable" condition as a missing key, fail loud.
    """
    if not os.path.exists(path):
        raise VendorNotConfiguredError(
            f"Schwab token store not found at {path!r}; run the OAuth authorization flow first."
        )
    with open(path) as fh:
        return TokenStore.from_dict(json.load(fh))


def save_store(path: str, store: TokenStore) -> None:
    """Write the token store atomically as ``0600`` (owner-only — refresh secret).

    Write a fresh sibling temp file (created empty at ``0600``, ``fchmod`` enforced
    before any secret bytes), then ``os.replace`` it over the target. This keeps the
    secret off a pre-existing loose-perm inode and makes the swap atomic, so a crash
    or a concurrent ``load_store`` never sees a truncated store.
    """
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # enforce perms while the file is still empty
        with os.fdopen(fd, "w") as fh:
            json.dump(store.to_dict(), fh, indent=2)
        os.replace(tmp, path)  # atomic on POSIX; original intact until this point
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _token_request(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token at the Schwab token endpoint.

    Module-level seam: unit tests ``monkeypatch.setattr(schwab_auth, "_token_request", ...)``
    to inject a canned response and never touch the network. Client credentials are
    read FIRST so a missing-app run fails loud before any socket opens.
    """
    client_id = os.getenv("SCHWAB_CLIENT_ID")
    client_secret = os.getenv("SCHWAB_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise VendorNotConfiguredError(
            "SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET are not set; cannot refresh the Schwab token."
        )
    response = requests.post(
        TOKEN_ENDPOINT,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def refresh_health(store: TokenStore, now: datetime) -> dict:
    """Report the 7-day refresh-token cadence at instant ``now``.

    ``needs_reauth_soon`` is the run-start trip wire (expired OR <24h left);
    ``refresh_expired`` means no automated refresh can succeed — manual re-auth only.
    """
    refresh_expires_at = store.refresh_issued_at + timedelta(seconds=REFRESH_TTL)
    seconds_left = (refresh_expires_at - now).total_seconds()
    return {
        "refresh_expires_at": refresh_expires_at,
        "seconds_until_refresh_expiry": seconds_left,
        "refresh_expired": seconds_left <= 0,
        "needs_reauth_soon": seconds_left <= REAUTH_WARN,
    }


def get_access_token(now: datetime, token_path: str | None = None) -> str:
    """Return a valid Schwab access token, refreshing from disk when needed.

    Order matters: a still-valid access token is returned as-is (it works now,
    even if the refresh token is near death); only once the access token has
    expired do we consult the refresh token — and a dead refresh token raises
    ``SchwabReauthRequiredError`` rather than attempting a doomed exchange.
    """
    path = resolve_token_path(token_path)
    store = load_store(path)

    if store.access_expires_at - timedelta(seconds=CLOCK_SKEW) > now:
        return store.access_token

    if refresh_health(store, now)["refresh_expired"]:
        raise SchwabReauthRequiredError(
            f"Schwab refresh token expired (issued {store.refresh_issued_at.isoformat()}); "
            "re-run the OAuth authorization flow."
        )

    resp = _token_request(store.refresh_token)
    store.access_token = resp["access_token"]
    # A missing/null/non-positive expires_in must not persist an already-stale token
    # (which would trigger a refresh storm) — fall back to the 30-min default.
    expires_in = resp.get("expires_in")
    if (not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool)
            or expires_in != expires_in or expires_in <= 0):  # NaN-safe (nan != nan)
        expires_in = ACCESS_TTL
    store.access_expires_at = now + timedelta(seconds=expires_in)

    # Refresh-token rotation invariant: reset the 7-day clock ONLY when Schwab
    # hands back a genuinely different token. Resetting it on an unchanged token
    # would silently grant an unbounded refresh window — the bug this guards.
    new_rt = resp.get("refresh_token")
    if new_rt and new_rt != store.refresh_token:
        store.refresh_token = new_rt
        store.refresh_issued_at = now

    save_store(path, store)
    return store.access_token
