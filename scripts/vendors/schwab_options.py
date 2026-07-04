"""P4 options facts from the Schwab chains endpoint (via TradingAgents-upstream).

IV rank is NOT obtainable from a chain snapshot; emits honest substitutes:
ATM IV near, IV term structure, put/call volume+OI ratios, notable OI, is_delayed.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from _common import emit, fact

from tradingagents.dataflows import schwab_options as upstream
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SRC = "schwab"
TERM_EXPIRIES = 4


def build_facts(env, top_oi):
    chain = env.data
    asof = (chain.as_of or env.provenance.fetched_at).isoformat()
    facts = {}
    # Drop Schwab -999.0 sentinel IVs (become -9.99 after /100) for IV facts only.
    clean = upstream.OptionsChain(
        symbol=chain.symbol,
        underlying_price=chain.underlying_price,
        as_of=chain.as_of,
        contracts=[c for c in chain.contracts if c.implied_volatility > 0],
    )
    near = clean.nearest_expiry()
    if near is not None:
        iv = clean.atm_iv(near)
        if iv is not None:
            facts["P4.atm_iv_near"] = fact(iv, "ratio", asof, SRC)
    term = []
    for exp in clean.expirations()[:TERM_EXPIRIES]:
        iv = clean.atm_iv(exp)
        if iv is not None:
            term.append([exp, iv])
    if term:
        facts["P4.iv_term"] = fact(term, "expiry_iv", asof, SRC)
    # Ratios use ALL contracts (volume/OI are unaffected by the IV sentinel).
    call_vol = sum(c.volume for c in chain.contracts if c.option_type == "CALL")
    put_vol = sum(c.volume for c in chain.contracts if c.option_type == "PUT")
    if call_vol > 0:
        facts["P4.put_call_volume_ratio"] = fact(put_vol / call_vol, "ratio", asof, SRC)
    call_oi = sum(c.open_interest for c in chain.contracts if c.option_type == "CALL")
    put_oi = sum(c.open_interest for c in chain.contracts if c.option_type == "PUT")
    if call_oi > 0:
        facts["P4.put_call_oi_ratio"] = fact(put_oi / call_oi, "ratio", asof, SRC)
    top = sorted(chain.contracts, key=lambda c: c.open_interest, reverse=True)[:top_oi]
    if top:
        rows = [
            {"expiry": c.expiration, "strike": c.strike, "type": c.option_type, "oi": c.open_interest}
            for c in top
        ]
        facts["P4.notable_oi"] = fact(rows, "contracts", asof, SRC)
    facts["P4.is_delayed"] = fact(env.provenance.is_delayed, "bool", asof, SRC)
    return facts


def _fail(msg, code):
    print(msg, file=sys.stderr)
    return code


def main(argv):
    parser = argparse.ArgumentParser(description="Schwab options-chain P4 facts")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--expiry-from", dest="expiry_from")
    parser.add_argument("--expiry-to", dest="expiry_to")
    parser.add_argument("--top-oi", dest="top_oi", type=int, default=5)
    args = parser.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    expiry_from = args.expiry_from or today.isoformat()
    expiry_to = args.expiry_to or (today + timedelta(days=60)).isoformat()
    try:
        env = upstream.SchwabOptionsVendor.fetch(args.ticker, expiry_from, expiry_to)
    except VendorNotConfiguredError as exc:  # incl. SchwabReauthRequiredError; before ValueError
        return _fail(str(exc), 2)
    except NoMarketDataError as exc:
        return _fail(str(exc), 3)
    except VendorRateLimitError as exc:
        return _fail(str(exc), 4)
    except Exception as exc:  # HTTP errors, sparse-chain KeyError/ValidationError, ...
        return _fail(f"{type(exc).__name__}: {exc}", 1)
    emit(build_facts(env, args.top_oi))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
