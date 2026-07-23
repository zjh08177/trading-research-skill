#!/usr/bin/env python3
"""Deterministic forward-return base rate after a comparable single-day move
— the pipeline's "no judgment single-samples" rule applied to the left/right-
side question: reports the forward distribution at THREE nested sample sizes
(raw occurrences, regime-clustered count, macro-cycle count) so a win-rate
can never be quoted without its own credibility discount attached. No
confidence interval is ever computed (at typical single-ticker macro-n < 5 it
would be fiction) — P9.base_rate_ci_note documents that plainly instead.
Stdlib only.

Usage: move_base_rate.py <datapack.json> <history.json>
Exit 0 ok; 3 on a missing required fact or too-short history; 2 on bad args."""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _movestats as ms  # noqa: E402

REQUIRED = ["P1.chg_pct_1d"]
MIN_BARS = 250
WINDOW_SESSIONS = 60
MACRO_GAP_DAYS = 545
HORIZONS = (5, 10, 20, 40, 60)


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(move_base_rate)"}


def build(pack, history):
    missing = [f for f in REQUIRED if fval(pack, f) is None]
    if missing:
        raise KeyError(", ".join(missing))
    today_chg = fval(pack, "P1.chg_pct_1d")

    dates, closes, _volumes = ms.parse_history(history)
    if len(closes) < MIN_BARS:
        raise ValueError(f"history too short ({len(closes)} bars < {MIN_BARS})")
    asof = dates[-1]

    returns = ms.daily_returns_pct(closes)
    direction = "down" if today_chg <= 0 else "up"
    threshold = abs(today_chg)
    all_event_idx = ms.comparable_event_indices(returns, direction, threshold)
    # Past occurrences only: today's own index has no forward data.
    past_event_idx = [i for i in all_event_idx if i < len(closes) - 1]

    stats = ms.forward_stats(closes, past_event_idx, horizons=HORIZONS)
    table = [{"horizon_days": h, "n": stats[h]["n"],
             "mean_pct": round(stats[h]["mean"], 1) if stats[h]["mean"] is not None else None,
             "median_pct": round(stats[h]["median"], 1) if stats[h]["median"] is not None else None,
             "winrate_pct": round(stats[h]["winrate"], 1) if stats[h]["winrate"] is not None else None,
             "avg_further_dd_pct": round(stats[h]["avg_dd"], 1) if stats[h]["avg_dd"] is not None else None,
             "worst_dd_pct": round(stats[h]["worst_dd"], 1) if stats[h]["worst_dd"] is not None else None}
             for h in HORIZONS]

    event_dates = [dates[i] for i in past_event_idx]
    clusters = ms.cluster_events(event_dates, dates, window_sessions=WINDOW_SESSIONS) if event_dates else []
    n_regimes = len(clusters)
    macro = ms.macro_cycles(clusters, gap_days=MACRO_GAP_DAYS) if clusters else []
    n_macro = len(macro)

    return {
        "P9.base_rate_n_raw": _fact(len(past_event_idx), "count", asof),
        "P9.base_rate_n_regimes": _fact(n_regimes, "count", asof),
        "P9.base_rate_n_macro": _fact(n_macro, "count", asof),
        "P9.base_rate_direction": _fact(direction, "label", asof),
        "P9.base_rate_threshold_pct": _fact(round(threshold, 2), "pct", asof),
        "P9.base_rate_ci_note": _fact(
            f"no confidence interval computed (n_macro={n_macro} independent "
            f"cycles is too few for one); treat the table as directional "
            f"corroboration, not a calibrated probability", "label", asof),
        "P9.base_rate_table": {"v": table, "unit": "table", "asof": asof,
                               "src": "derived(move_base_rate)"},
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write("usage: move_base_rate.py <datapack.json> <history.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    with open(argv[1]) as f:
        history = json.load(f)
    try:
        facts = build(pack, history)
    except KeyError as e:
        sys.stderr.write(f"ERROR: move_base_rate.py needs missing fact(s): {e.args[0]}\n")
        return 3
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
