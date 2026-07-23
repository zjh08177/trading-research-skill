#!/usr/bin/env python3
"""Deterministic exhaustion-turning-condition tally: precomputes the 4 named
booleans from prompts.md's counter-trend-trigger grammar (RSI turn, volume
climax-then-decay, consecutive closes with no new ATR-magnitude adverse day,
and — clustered regimes only — a crash/melt-free window) plus a k/4 tally, so
judges cite a precomputed fact instead of hand-counting. Hand-counting is the
actual defect this fixes: a live run's judges unanimously miscounted "ATR
stretch" (the thesis PRECONDITION, prompts.md's stretch.py block) as one of
the 4 CONDITIONS, even though the prose never lists it as one. Runs after
stretch.py/percentile.py/volume_climax.py/move_cluster.py have already merged
their P9 facts into the pack — reads P9.stretch_sma50_atr (which side of
trend price is stretched to — the reversal direction to check, NOT today's
single-day tick sign), P9.rsi_percentile_conditional, P9.volume_decay_flag,
and P9.cluster_status from the pack rather than recomputing them (single
source of truth per fact). Stdlib only.

Usage: exhaustion.py <datapack.json> <history.json>
Exit 0 ok; 3 on a missing required fact or too-short history; 2 on bad args."""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _movestats as ms  # noqa: E402

REQUIRED = ["P1.chg_pct_1d", "P2.atr14", "P9.stretch_sma50_atr"]
MIN_BARS = 60
RSI_TURN_LOOKBACK = 5      # sessions searched for the recent RSI extreme
RSI_TURN_PTS = 5.0         # minimum points-off-extreme to count as "turning"
DECILE_LO, DECILE_HI = 10.0, 90.0
CLOSES_STREAK = 3          # consecutive higher/lower closes required
CRASHFREE_SESSIONS = 10    # minimum gap since the prior comparable event


def fval(pack, fid):
    f = pack.get(fid)
    if not isinstance(f, dict):
        return None
    v = f.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def sval(pack, fid):
    f = pack.get(fid)
    return f.get("v") if isinstance(f, dict) else None


def _fact(v, unit, asof):
    return {"v": v, "unit": unit, "asof": asof, "src": "derived(exhaustion)"}


def _rsi_turn(rsi_series, direction):
    """RSI14 turns >=5pts off its recent (last RSI_TURN_LOOKBACK bars) extreme
    in the reversal direction. Returns False (never None) when the series is
    too short — a missing signal is never silently omitted from the tally."""
    window = [r for r in rsi_series[-RSI_TURN_LOOKBACK:] if r is not None]
    today_rsi = rsi_series[-1] if rsi_series else None
    if today_rsi is None or not window:
        return False
    if direction == "down":  # oversold -> turning up
        return (today_rsi - min(window)) >= RSI_TURN_PTS
    return (max(window) - today_rsi) >= RSI_TURN_PTS  # overbought -> turning down


def _bottom_or_top_decile(conditional_pctile, direction):
    if conditional_pctile is None:
        return False
    return conditional_pctile <= DECILE_LO if direction == "down" else conditional_pctile >= DECILE_HI


def _closes_streak_no_adverse_atr(closes, direction, atr14):
    """3 consecutive higher (direction=down, reversal=up) or lower
    (direction=up, reversal=down) closes, with no day in that window whose
    adverse-direction $ move was >= 1x ATR14."""
    if len(closes) < CLOSES_STREAK + 1 or atr14 is None:
        return False
    window = closes[-(CLOSES_STREAK + 1):]
    diffs = [window[i] - window[i - 1] for i in range(1, len(window))]
    if direction == "down":  # want a higher-closes streak
        streak = all(d > 0 for d in diffs)
        no_adverse = all(d > -atr14 for d in diffs)  # no new >=1x ATR DOWN day
    else:  # direction == "up" -> want a lower-closes streak
        streak = all(d < 0 for d in diffs)
        no_adverse = all(d < atr14 for d in diffs)  # no new >=1x ATR UP day
    return bool(streak and no_adverse)


def _crashfree_window(dates, returns, direction, threshold_pct, cluster_status):
    """Clustered-regimes-only condition: vacuously True when isolated (no
    cluster to have a fresh crash/melt-up inside), else True only when the
    most recent comparable-magnitude event before today is >=CRASHFREE_SESSIONS
    sessions back."""
    if cluster_status != "clustered":
        return True
    idx = ms.comparable_event_indices(returns, direction, threshold_pct)
    prior = [i for i in idx if i < len(dates) - 1]  # exclude today itself
    if not prior:
        return True
    gap = (len(dates) - 1) - max(prior)
    return gap >= CRASHFREE_SESSIONS


def build(pack, history):
    missing = [f for f in REQUIRED if fval(pack, f) is None]
    if missing:
        raise KeyError(", ".join(missing))
    today_chg = fval(pack, "P1.chg_pct_1d")
    atr14 = fval(pack, "P2.atr14")
    # Direction is the STRETCH regime (price below/above trend, per stretch.py),
    # not today's single-day tick sign — a bounce day (today_chg > 0) inside an
    # ongoing oversold stretch is still "oversold_turning_up", never flips to
    # overbought just because today happened to close green.
    stretch_sma50 = fval(pack, "P9.stretch_sma50_atr")
    direction = "down" if stretch_sma50 <= 0 else "up"
    exhaustion_direction = "oversold_turning_up" if direction == "down" else "overbought_turning_down"

    dates, closes, _volumes = ms.parse_history(history)
    if len(closes) < MIN_BARS:
        raise ValueError(f"history too short ({len(closes)} bars < {MIN_BARS})")
    asof = dates[-1]

    rsi_series = ms.rsi14(closes)
    returns = ms.daily_returns_pct(closes)

    conditional_pctile = fval(pack, "P9.rsi_percentile_conditional")
    volume_decay = sval(pack, "P9.volume_decay_flag")
    cluster_status = sval(pack, "P9.cluster_status")

    rsi_turn = _rsi_turn(rsi_series, direction) and _bottom_or_top_decile(conditional_pctile, direction)
    vol_decay = bool(volume_decay) if isinstance(volume_decay, bool) else False
    higher_closes = _closes_streak_no_adverse_atr(closes, direction, atr14)
    crashfree = _crashfree_window(dates, returns, direction, abs(today_chg), cluster_status)

    k = sum([rsi_turn, vol_decay, higher_closes, crashfree])

    return {
        "P9.exhaustion_direction": _fact(exhaustion_direction, "label", asof),
        "P9.exhaustion_rsi_turn": _fact(rsi_turn, "bool", asof),
        "P9.exhaustion_vol_decay": _fact(vol_decay, "bool", asof),
        "P9.exhaustion_higher_closes": _fact(higher_closes, "bool", asof),
        "P9.exhaustion_crashfree_window": _fact(crashfree, "bool", asof),
        "P9.exhaustion_tally": _fact(f"{k}/4", "ratio_label", asof),
        "P9.exhaustion_tally_k": _fact(k, "count", asof),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        sys.stderr.write("usage: exhaustion.py <datapack.json> <history.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    with open(argv[1]) as f:
        history = json.load(f)
    try:
        facts = build(pack, history)
    except KeyError as e:
        sys.stderr.write(f"ERROR: exhaustion.py needs missing fact(s): {e.args[0]}\n")
        return 3
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3
    sys.stdout.write(json.dumps(facts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
