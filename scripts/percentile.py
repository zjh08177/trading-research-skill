#!/usr/bin/env python3
"""Deterministic RSI-percentile block: today's RSI14 percentile against all
history AND against history conditioned on a comparably-sized same-direction
single-day move — the check that stops an analyst from narrating "RSI
approaching oversold" as edge when the conditional reading is unremarkable.
Stdlib only.

Usage: percentile.py <datapack.json> <history.json>
Exit 0 ok; 3 on a missing required fact or too-short history; 2 on bad args."""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _movestats as ms  # noqa: E402

REQUIRED = ["P2.rsi14", "P1.chg_pct_1d", "P2.atr14_pct"]
MIN_BARS = 60
MIN_CONDITIONAL_N = 5
NO_EDGE_BAND = (40.0, 60.0)
MOVE_THRESHOLD_FACTOR = 0.5  # "comparable" = >= half of today's move magnitude


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(percentile)"}


def build(pack, history):
    missing = [f for f in REQUIRED if fval(pack, f) is None]
    if missing:
        raise KeyError(", ".join(missing))
    asof = pack["P2.rsi14"]["asof"]
    today_rsi = fval(pack, "P2.rsi14")
    today_chg = fval(pack, "P1.chg_pct_1d")

    dates, closes, _volumes = ms.parse_history(history)
    if len(closes) < MIN_BARS:
        raise ValueError(f"history too short ({len(closes)} bars < {MIN_BARS})")

    rsi_series = ms.rsi14(closes)
    rsi_all = [r for r in rsi_series if r is not None]
    rsi_all_pctile = ms.percentile_rank(today_rsi, rsi_all)

    returns = ms.daily_returns_pct(closes)
    direction = "down" if today_chg <= 0 else "up"
    threshold = abs(today_chg) * MOVE_THRESHOLD_FACTOR
    event_idx = ms.comparable_event_indices(returns, direction, threshold)
    conditional_pop = [rsi_series[i] for i in event_idx if rsi_series[i] is not None]

    facts = {
        "P9.rsi_percentile_all": _fact(round(rsi_all_pctile, 1), "pctile", asof),
    }
    if len(conditional_pop) >= MIN_CONDITIONAL_N:
        cond_pctile = ms.percentile_rank(today_rsi, conditional_pop)
        note = "no_edge" if NO_EDGE_BAND[0] <= cond_pctile <= NO_EDGE_BAND[1] else "differentiating"
        facts["P9.rsi_percentile_conditional"] = _fact(round(cond_pctile, 1), "pctile", asof)
        facts["P9.rsi_percentile_note"] = _fact(note, "label", asof)
    else:
        facts["P9.rsi_percentile_conditional"] = _fact(None, "pctile", asof)
        facts["P9.rsi_percentile_note"] = _fact("insufficient_sample", "label", asof)
    facts["P9.rsi_percentile_conditional_n"] = _fact(len(conditional_pop), "count", asof)
    return facts


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write("usage: percentile.py <datapack.json> <history.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    with open(argv[1]) as f:
        history = json.load(f)
    try:
        facts = build(pack, history)
    except KeyError as e:
        sys.stderr.write(f"ERROR: percentile.py needs missing fact(s): {e.args[0]}\n")
        return 3
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
