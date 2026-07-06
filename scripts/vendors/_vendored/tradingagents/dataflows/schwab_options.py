"""Schwab options-chain vendor (W2 T2.3).

Live ``get_options_chain`` vendor over the Schwab Market Data ``chains`` endpoint,
returning a provenance-stamped :class:`Envelope` of :class:`OptionsChain`.

Options are an OPTIONAL/non-core enrichment (``options_chain`` is in
``OPTIONAL_CATEGORIES``): a vendor failure DEGRADES the run, it never run-level
ABSTAINs. Mirrors ``schwab.py`` exactly -- the network seam is the module-level
``_request(path, params)`` (patch ``mock.patch.object(schwab_options, "_request",
...)`` to drive the parser offline) and the token is read FIRST so a
missing-credentials run raises ``VendorNotConfiguredError`` before any socket.

LIVE-UNTESTABLE in W2: Schwab OAuth app provisioning (P1) is still pending, so
this module is exercised ONLY against a canned ``_request`` payload + the on-disk
fixture. Live verification is deferred to EC11/Tier-B.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests
from pydantic import BaseModel

from .config import get_config
from .envelope import Envelope, Provenance
from .errors import NoMarketDataError, VendorRateLimitError
from .schwab import _get_access_token  # reuse the shared Schwab token reader

# Schwab Market Data options-chain path (callExpDateMap/putExpDateMap payload).
SCHWAB_CHAINS_PATH = "https://api.schwabapi.com/marketdata/v1/chains"
REQUEST_TIMEOUT = 15


class OptionContract(BaseModel):
    """A single option contract row (one strike/expiry/type)."""

    expiration: str
    strike: float
    option_type: str
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


class OptionsChain(BaseModel):
    """A symbol's option chain plus the underlying snapshot it was priced against.

    ``as_of`` is the instant the vendor asserts the chain was true (never
    ``now()``); it stays ``None`` only when the vendor supplied no quote time.
    Helpers are pure -- they read ``contracts`` and never touch I/O.
    """

    symbol: str
    underlying_price: float
    as_of: datetime | None = None
    contracts: list[OptionContract]

    def expirations(self) -> list[str]:
        """Sorted unique expiration dates (``YYYY-MM-DD`` strings sort lexically)."""
        return sorted({c.expiration for c in self.contracts})

    def nearest_expiry(self) -> str | None:
        """The earliest expiration, or ``None`` for an empty chain."""
        exps = self.expirations()
        return exps[0] if exps else None

    def atm_contract(self, expiry: str, option_type: str = "CALL") -> OptionContract | None:
        """The contract of ``option_type`` nearest the underlying for ``expiry``."""
        candidates = [
            c
            for c in self.contracts
            if c.expiration == expiry and c.option_type == option_type
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.strike - self.underlying_price))

    def atm_iv(self, expiry: str) -> float | None:
        """The implied volatility of the ATM call for ``expiry`` (``None`` if absent)."""
        contract = self.atm_contract(expiry)
        return contract.implied_volatility if contract else None


def _request(path: str, params: dict) -> dict:
    """GET the Schwab endpoint with the bearer token; classify failure by behavior.

    Module-level for seam-uniformity with ``schwab.py``: the unit tests
    ``mock.patch.object(schwab_options, "_request", ...)`` to inject a canned
    chains payload and never touch the network. The token is read FIRST so a
    missing-credentials run raises ``VendorNotConfiguredError`` before any socket
    is opened.
    """
    token = _get_access_token()
    response = requests.get(
        path,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 429:
        raise VendorRateLimitError("Schwab rate limit: HTTP 429.")
    response.raise_for_status()
    return response.json()


def _chain_params(symbol: str, expiry_from: str | None, expiry_to: str | None) -> dict:
    """Build options-chain query params for the Schwab endpoint."""
    params: dict = {"symbol": symbol.upper(), "contractType": "ALL"}
    if expiry_from:
        params["fromDate"] = expiry_from
    if expiry_to:
        params["toDate"] = expiry_to
    return params


def _quote_time(payload: dict) -> datetime | None:
    """Vendor as-of from the payload quote time (epoch ms), NEVER ``now()``.

    Prefers a top-level ``quoteTime`` then ``underlying.quoteTime``; returns
    ``None`` (flagged unverified downstream) when the vendor supplied neither.
    """
    qt = payload.get("quoteTime")
    if qt is None:
        qt = (payload.get("underlying") or {}).get("quoteTime")
    if qt is None:
        return None
    return datetime.fromtimestamp(qt / 1000, tz=timezone.utc)


def _parse_exp_map(exp_map: dict, option_type: str) -> list[OptionContract]:
    """Flatten one Schwab ``{exp_key: {strike: [contract]}}`` map to contracts.

    ``exp_key`` is ``"YYYY-MM-DD:DTE"`` -> the expiration date is its prefix.
    NORMALIZES the Schwab ``volatility`` PERCENT to a decimal
    ``implied_volatility`` (45.0 -> 0.45).
    """
    contracts: list[OptionContract] = []
    for exp_key, strike_map in exp_map.items():
        expiration = exp_key.split(":")[0]
        for strike_str, rows in strike_map.items():
            for c in rows:
                contracts.append(
                    OptionContract(
                        expiration=expiration,
                        strike=c.get("strikePrice", strike_str),
                        option_type=option_type,
                        bid=c["bid"],
                        ask=c["ask"],
                        last=c["last"],
                        volume=c.get("totalVolume", 0),
                        open_interest=c.get("openInterest", 0),
                        implied_volatility=c["volatility"] / 100.0,
                        delta=c["delta"],
                        gamma=c["gamma"],
                        theta=c["theta"],
                        vega=c["vega"],
                        rho=c["rho"],
                    )
                )
    return contracts


class SchwabOptionsVendor:
    """Parse the Schwab chains response into a stamped ``Envelope[OptionsChain]``."""

    endpoint = SCHWAB_CHAINS_PATH

    @classmethod
    def fetch(
        cls,
        symbol: str,
        expiry_from: str | None = None,
        expiry_to: str | None = None,
    ) -> Envelope[OptionsChain]:
        """Fetch the chain and wrap it with REAL provenance from the response.

        Raises ``NoMarketDataError`` when Schwab reports a non-SUCCESS status or
        both expiry maps are empty.
        """
        payload = _request(cls.endpoint, _chain_params(symbol, expiry_from, expiry_to))
        call_map = payload.get("callExpDateMap") or {}
        put_map = payload.get("putExpDateMap") or {}
        if payload.get("status") != "SUCCESS" or not (call_map or put_map):
            raise NoMarketDataError(symbol, None, "Schwab returned no option chain")

        contracts = _parse_exp_map(call_map, "CALL") + _parse_exp_map(put_map, "PUT")
        vendor_as_of = _quote_time(payload)
        chain = OptionsChain(
            symbol=symbol.upper(),
            underlying_price=payload["underlyingPrice"],
            as_of=vendor_as_of,
            contracts=contracts,
        )

        # vendor_as_of comes from the API quote time (never now()); is_delayed
        # reflects the feed (Schwab options are delayed unless the payload says so).
        provenance = Provenance(
            vendor="schwab",
            endpoint=cls.endpoint,
            fetched_at=datetime.now(timezone.utc),
            vendor_as_of=vendor_as_of,
            is_delayed=bool(payload.get("isDelayed", True)),
            source="SCHWAB",
        )
        return Envelope[OptionsChain](data=chain, provenance=provenance)


def render_options_markdown(
    env: Envelope[OptionsChain],
    expiry_from: str | None = None,
    expiry_to: str | None = None,
) -> str:
    """Routed-tool markdown: underlying snapshot + per-expiry ATM call/put greeks.

    Shared by both options vendors so ``get_options_chain`` emits the same shape
    regardless of vendor. The provenance citation is rendered inline so the
    analyst can cite where/as-of the chain was true.
    """
    chain = env.data
    exps = chain.expirations()
    if expiry_from:
        exps = [e for e in exps if e >= expiry_from]
    if expiry_to:
        exps = [e for e in exps if e <= expiry_to]

    lines = [
        f"## Options chain: {chain.symbol}",
        f"Underlying price: {chain.underlying_price}",
        f"Provenance: {env.citation()}",
        "",
    ]
    if not exps:
        lines.append("No expirations in the requested window.")
        return "\n".join(lines) + "\n"

    for exp in exps:
        iv = chain.atm_iv(exp)
        iv_str = f"{iv:.1%}" if iv is not None else "n/a"
        lines.append(f"### Expiry {exp} — ATM IV {iv_str}")
        lines.append(
            "| type | strike | bid | ask | last | IV | delta | gamma | theta | vega | rho | vol | OI |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for option_type in ("CALL", "PUT"):
            c = chain.atm_contract(exp, option_type)
            if c is None:
                continue
            lines.append(
                f"| {c.option_type} | {c.strike} | {c.bid} | {c.ask} | {c.last} | "
                f"{c.implied_volatility:.4f} | {c.delta} | {c.gamma} | {c.theta} | "
                f"{c.vega} | {c.rho} | {c.volume} | {c.open_interest} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def get_options_chain(
    symbol: str,
    expiry_from: str | None = None,
    expiry_to: str | None = None,
) -> str:
    """Routed ``get_options_chain`` contract: markdown chain for the symbol (Schwab)."""
    env = SchwabOptionsVendor.fetch(symbol, expiry_from, expiry_to)
    return render_options_markdown(env, expiry_from, expiry_to)


def load_options_envelope(symbol: str, curr_date: str) -> Envelope[OptionsChain]:
    """Vendor-aware ``Envelope[OptionsChain]`` loader for the in-node options snapshot.

    Honors the configured ``data_vendors['options_chain']`` vendor (``fixture`` or
    ``schwab``); ``curr_date`` is accepted for loader-seam uniformity (the chain is
    a current snapshot, so no look-ahead filtering applies here).
    """
    config = get_config()
    vendor = config.get("data_vendors", {}).get("options_chain", "")
    vendor = vendor.split(",")[0].strip() if vendor else ""
    if vendor == "fixture":
        from .fixture_options import FixtureOptionsVendor

        return FixtureOptionsVendor.fetch(symbol)
    return SchwabOptionsVendor.fetch(symbol)
