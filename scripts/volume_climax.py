#!/usr/bin/env python3
"""Deterministic volume-climax block: today's volume z-score vs the trailing
60 sessions, a climax flag (z >= 2), and a climax-then-decay flag (a >=2sigma
volume day within the last 3 sessions has since decayed to <=60% of its
volume). Capitulation is a volume event, not just a price event. Stdlib only.

Usage: volume_climax.py <datapack.json> <history.json>
Exit 0 ok; 3 on too-short history; 2 on bad args."""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _movestats as ms  # noqa: E402

MIN_BARS = 61
WINDOW = 60
Z_CLIMAX = 2.0
DECAY_LOOKBACK = 3
DECAY_RATIO = 0.60


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(volume_climax)"}


def build(pack, history):
    dates, _closes, volumes = ms.parse_history(history)
    if len(volumes) < MIN_BARS:
        raise ValueError(f"history too short ({len(volumes)} bars < {MIN_BARS})")
    asof = dates[-1]

    def zscore_at(i):
        pop = volumes[max(0, i - WINDOW):i]
        return ms.zscore(volumes[i], pop) if pop else None

    today_z = zscore_at(len(volumes) - 1)
    climax_today = today_z is not None and today_z >= Z_CLIMAX

    decay = False
    for k in range(1, DECAY_LOOKBACK + 1):
        i = len(volumes) - 1 - k
        if i < WINDOW:
            continue
        z = zscore_at(i)
        if z is not None and z >= Z_CLIMAX:
            if volumes[-1] <= volumes[i] * DECAY_RATIO:
                decay = True
                break

    return {
        "P9.volume_zscore": _fact(round(today_z, 2) if today_z is not None else None,
                                  "sigma", asof),
        "P9.volume_climax_flag": _fact(climax_today, "bool", asof),
        "P9.volume_decay_flag": _fact(decay, "bool", asof),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write("usage: volume_climax.py <datapack.json> <history.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    with open(argv[1]) as f:
        history = json.load(f)
    try:
        facts = build(pack, history)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
