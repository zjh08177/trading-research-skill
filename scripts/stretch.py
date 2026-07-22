#!/usr/bin/env python3
"""Deterministic left-side/right-side stretch block: distance from
SMA20/50/200 in ATR14 (and, for SMA50, sigma30) multiples, today's move
normalized by ATR14, a climax flag, and (only with --leverage > 1) the
leveraged-product daily variance-drag figure. Stdlib only.

Usage: stretch.py <datapack.json> [--leverage N]
Exit 0 ok; 3 on a missing required fact (fail loud, never fabricate); 2 on
bad args."""
import argparse
import json
import sys

REQUIRED = ["P1.price", "P2.sma20", "P2.sma50", "P2.sma200", "P2.atr14",
            "P2.atr14_pct", "P2.sigma30", "P1.chg_pct_1d"]
CLIMAX_ATR = 1.5


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(stretch)"}


def build(pack, leverage=1):
    price = fval(pack, "P1.price")
    if price is None:
        price = fval(pack, "P1.last")
    missing = [f for f in REQUIRED if f != "P1.price" and fval(pack, f) is None]
    if price is None:
        missing.append("P1.price|P1.last")
    if missing:
        raise KeyError(", ".join(missing))
    asof = pack["P2.sma50"]["asof"]
    sma20, sma50, sma200 = (fval(pack, "P2.sma20"), fval(pack, "P2.sma50"),
                            fval(pack, "P2.sma200"))
    atr, atr_pct, sigma = (fval(pack, "P2.atr14"), fval(pack, "P2.atr14_pct"),
                           fval(pack, "P2.sigma30"))
    chg = fval(pack, "P1.chg_pct_1d")

    # atr==0 is a legitimate present-but-zero value (it passes the "is None"
    # required-fact check above) — guard each ATR-normalized stretch fact the
    # same way this file already guards move_atr against atr_pct==0 below
    # (and the way risk_box.py guards its own move_atr against atr_pct==0):
    # emit None ("cannot normalize") instead of raising ZeroDivisionError.
    # 2dp everywhere: these facts are quoted verbatim in the report prose, and an
    # unrounded "2.424549120275739 ATR14 above SMA50" once shipped in a published
    # executive summary. Siblings (percentile.py, move_base_rate.py,
    # volume_climax.py) already round; qa_check.py's 0.5% tolerance absorbs it.
    facts = {
        "P9.stretch_sma20_atr": _fact(round((price - sma20) / atr, 2) if atr else None, "ATRs", asof),
        "P9.stretch_sma50_atr": _fact(round((price - sma50) / atr, 2) if atr else None, "ATRs", asof),
        "P9.stretch_sma200_atr": _fact(round((price - sma200) / atr, 2) if atr else None, "ATRs", asof),
        "P9.stretch_sma50_sigma": _fact(
            round(((price - sma50) / sma50 * 100) / sigma, 2) if sigma else None,
            "sigma30_multiples", asof),
    }
    # rounded BEFORE the climax test so the emitted fact and the flag agree
    move_atr = round(chg / atr_pct, 2) if atr_pct else None
    facts["P9.move_atr"] = _fact(move_atr, "ATRs", asof)
    climax = move_atr is not None and abs(move_atr) >= CLIMAX_ATR
    facts["P9.climax"] = _fact(climax, "bool", asof)
    facts["P9.climax_direction"] = _fact(
        ("down" if move_atr < 0 else "up") if climax else None, "label", asof)

    if leverage and leverage > 1 and sigma is not None:
        sigma_underlying = sigma / leverage / 100
        drag_pct = (leverage ** 2 - leverage) / 2 * sigma_underlying ** 2 * 100
        facts["P9.decay_risk_daily_pct"] = _fact(round(drag_pct, 4), "pct/day", asof)
    return facts


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("datapack")
    parser.add_argument("--leverage", type=int, default=1)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    with open(args.datapack) as f:
        pack = json.load(f)
    try:
        facts = build(pack, args.leverage)
    except KeyError as e:
        sys.stderr.write(f"ERROR: stretch.py needs missing fact(s): {e.args[0]}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
