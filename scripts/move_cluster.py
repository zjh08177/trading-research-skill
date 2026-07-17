#!/usr/bin/env python3
"""Deterministic regime-cluster classifier: is today's move part of an
isolated event or a cluster of comparable-or-greater same-direction moves in
the trailing ~60 sessions? A clustered crash/melt-up is a regime, not a
single capitulation/blow-off print — the Mean-Reversion analyst is forbidden
from using capitulation/blow-off language for a single day inside a cluster.
Stdlib only.

Usage: move_cluster.py <datapack.json> <history.json>
Exit 0 ok; 3 on a missing required fact or too-short history; 2 on bad args."""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _movestats as ms  # noqa: E402

REQUIRED = ["P1.chg_pct_1d"]
MIN_BARS = 60
WINDOW_SESSIONS = 60


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(move_cluster)"}


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
    event_idx = ms.comparable_event_indices(returns, direction, threshold)
    event_dates = [dates[i] for i in event_idx]
    if dates[-1] not in event_dates:
        event_dates.append(dates[-1])  # today is always its own event

    clusters = ms.cluster_events(event_dates, dates, window_sessions=WINDOW_SESSIONS)
    today_cluster = next(c for c in clusters if dates[-1] in c)
    k = len(today_cluster)

    return {
        "P9.cluster_status": _fact("clustered" if k > 1 else "isolated", "label", asof),
        "P9.cluster_k": _fact(k, "count", asof),
        "P9.cluster_events_n": _fact(len(event_dates), "count", asof),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write("usage: move_cluster.py <datapack.json> <history.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    with open(argv[1]) as f:
        history = json.load(f)
    try:
        facts = build(pack, history)
    except KeyError as e:
        sys.stderr.write(f"ERROR: move_cluster.py needs missing fact(s): {e.args[0]}\n")
        return 3
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
