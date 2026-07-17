#!/usr/bin/env python3
"""Deterministic 2-of-3 live-price cross-check resolution: when schwab's live
quote (P1.last) and tiingo's independent live oracle (P1.px_last_oob)
disagree beyond tolerance, a third independent source
(vendors/finnhub_oracle.py's P1.px_finnhub_oob) is consulted to break the
tie deterministically, rather than leaving an unresolved CROSS-CHECK FAIL
for judges to adjudicate through.

Scope: LIVE price only (current-day runs) — Finnhub's /quote endpoint has no
as-of parameter, so this cannot cross-check a SETTLED historical close; a
back-dated run's settled-close cross-check (tiingo px_close_oob vs schwab
P1.price) has only 2 sources and stays a 2-source CROSS-CHECK OK/FAIL as
before (unchanged from the pre-existing invariant 10 behavior). Stdlib only.

Usage: price_crosscheck.py <datapack.json> [--tolerance-pct 0.5]
Exit 0 always (this never fails the pipeline — it only resolves or discloses
a discrepancy); 2 on bad args."""
import argparse
import json
import sys

DEFAULT_TOLERANCE_PCT = 0.5


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(price_crosscheck)"}


def _agree(a, b, tolerance_pct):
    if a is None or b is None or b == 0:
        return False
    return abs(a - b) / abs(b) * 100 <= tolerance_pct


def build(pack, tolerance_pct=DEFAULT_TOLERANCE_PCT):
    schwab = fval(pack, "P1.last")
    tiingo = fval(pack, "P1.px_last_oob")
    finnhub = fval(pack, "P1.px_finnhub_oob")
    asof = (pack.get("P1.last") or pack.get("P1.px_last_oob")
            or pack.get("P1.px_finnhub_oob") or {}).get("asof", "")

    if schwab is None or tiingo is None:
        return {"P1.crosscheck_status": _fact("unavailable", "label", asof),
                "P1.crosscheck_note": _fact(
                    "schwab and/or tiingo live price missing — 2-source "
                    "baseline cross-check could not even run", "label", asof)}

    if _agree(schwab, tiingo, tolerance_pct):
        return {"P1.crosscheck_status": _fact("ok", "label", asof),
                "P1.crosscheck_note": _fact(
                    f"schwab {schwab} vs tiingo {tiingo} agree within "
                    f"{tolerance_pct}%", "label", asof)}

    # schwab vs tiingo disagree — this is the case item 8 fixes: don't leave
    # it unresolved. Bring in the 3rd source if it was fetched.
    if finnhub is None:
        return {"P1.crosscheck_status": _fact("fail_unresolved", "label", asof),
                "P1.crosscheck_note": _fact(
                    f"schwab {schwab} vs tiingo {tiingo} disagree "
                    f"(>{tolerance_pct}%); 3rd source (finnhub) unavailable "
                    f"this run — disclosed, unresolved", "label", asof)}

    schwab_finnhub = _agree(schwab, finnhub, tolerance_pct)
    tiingo_finnhub = _agree(tiingo, finnhub, tolerance_pct)

    if schwab_finnhub and not tiingo_finnhub:
        return {
            "P1.crosscheck_status": _fact("resolved_2of3", "label", asof),
            "P1.crosscheck_resolved_price": _fact(schwab, "USD", asof),
            "P1.crosscheck_note": _fact(
                f"schwab {schwab} + finnhub {finnhub} agree, outvoting "
                f"tiingo {tiingo} — resolved to schwab/finnhub", "label", asof),
        }
    if tiingo_finnhub and not schwab_finnhub:
        return {
            "P1.crosscheck_status": _fact("resolved_2of3", "label", asof),
            "P1.crosscheck_resolved_price": _fact(tiingo, "USD", asof),
            "P1.crosscheck_note": _fact(
                f"tiingo {tiingo} + finnhub {finnhub} agree, outvoting "
                f"schwab {schwab} — resolved to tiingo/finnhub", "label", asof),
        }
    if schwab_finnhub and tiingo_finnhub:
        # finnhub sits between both within tolerance of each independently,
        # but schwab and tiingo don't agree with each other -> still a
        # genuine 3-way spread, not a clean majority; disclose rather than
        # silently pick one.
        return {"P1.crosscheck_status": _fact("fail_3way", "label", asof),
                "P1.crosscheck_note": _fact(
                    f"schwab {schwab}, tiingo {tiingo}, finnhub {finnhub}: "
                    f"finnhub is within tolerance of both individually but "
                    f"schwab/tiingo disagree with each other — genuine "
                    f"3-way spread, disclosed as an open discrepancy", "label", asof)}

    return {"P1.crosscheck_status": _fact("fail_3way", "label", asof),
            "P1.crosscheck_note": _fact(
                f"schwab {schwab}, tiingo {tiingo}, finnhub {finnhub}: all "
                f"three pairwise disagree beyond {tolerance_pct}% — "
                f"unresolvable 3-way split, disclosed as an open "
                f"discrepancy fact, never silently picked", "label", asof)}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("datapack")
    parser.add_argument("--tolerance-pct", type=float, default=DEFAULT_TOLERANCE_PCT)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    with open(args.datapack) as f:
        pack = json.load(f)
    facts = build(pack, args.tolerance_pct)
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
