import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import exhaustion as mod


# ---- pure helper functions --------------------------------------------

def test_rsi_turn_true_when_off_recent_low_by_5pts():
    series = [50, 40, 30, 28, 33]  # today (33) is 5pts off the recent low (28)
    assert mod._rsi_turn(series, "down") is True


def test_rsi_turn_false_when_still_near_low():
    series = [50, 40, 30, 28, 30]  # only 2pts off the low
    assert mod._rsi_turn(series, "down") is False


def test_rsi_turn_mirrors_for_up_direction():
    series = [50, 60, 70, 72, 66]  # 6pts down off the recent high (72)
    assert mod._rsi_turn(series, "up") is True


def test_rsi_turn_false_on_empty_series():
    assert mod._rsi_turn([], "down") is False


def test_bottom_decile_true_below_threshold():
    assert mod._bottom_or_top_decile(5.0, "down") is True
    assert mod._bottom_or_top_decile(15.0, "down") is False


def test_top_decile_true_above_threshold():
    assert mod._bottom_or_top_decile(95.0, "up") is True
    assert mod._bottom_or_top_decile(85.0, "up") is False


def test_decile_false_on_none():
    assert mod._bottom_or_top_decile(None, "down") is False


def test_closes_streak_higher_no_adverse_atr_true():
    closes = [100.0, 101.0, 102.5, 104.0]  # 3 consecutive higher closes
    assert mod._closes_streak_no_adverse_atr(closes, "down", atr14=5.0) is True


def test_closes_streak_broken_by_a_down_day():
    closes = [100.0, 101.0, 100.5, 104.0]  # not strictly increasing
    assert mod._closes_streak_no_adverse_atr(closes, "down", atr14=5.0) is False


def test_closes_streak_fails_on_new_atr_down_day():
    closes = [110.0, 111.0, 100.0, 104.0]  # a >=1x ATR down day inside the window
    assert mod._closes_streak_no_adverse_atr(closes, "down", atr14=5.0) is False


def test_closes_streak_mirrors_lower_closes_for_up_direction():
    closes = [100.0, 99.0, 97.5, 96.0]
    assert mod._closes_streak_no_adverse_atr(closes, "up", atr14=5.0) is True


def test_crashfree_window_vacuously_true_when_isolated():
    assert mod._crashfree_window(["d"] * 20, [None] * 20, "down", 10.0, "isolated") is True


def test_crashfree_window_true_when_no_prior_events():
    dates = [f"d{i}" for i in range(20)]
    returns = [None] + [0.1] * 19
    assert mod._crashfree_window(dates, returns, "down", 10.0, "clustered") is True


def test_crashfree_window_false_when_recent_comparable_event():
    dates = [f"d{i}" for i in range(20)]
    returns = [None] * 15 + [-11.0] + [0.1] * 4  # a comparable event 4 sessions back
    assert mod._crashfree_window(dates, returns, "down", 10.0, "clustered") is False


def test_crashfree_window_true_when_prior_event_far_enough_back():
    dates = [f"d{i}" for i in range(20)]
    returns = [-11.0] + [0.1] * 19  # comparable event 19 sessions back
    assert mod._crashfree_window(dates, returns, "down", 10.0, "clustered") is True


# ---- end-to-end build() --------------------------------------------

def _bars(closes, start_year=2024):
    bars = []
    for i, c in enumerate(closes):
        bars.append({"date": f"{start_year}-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
                     "close": c, "adjClose": c, "volume": 1_000_000})
    return {"ticker": "X", "asof": bars[-1]["date"], "bars": bars}


def _pack(chg, atr14=5.0, conditional_pctile=5.0, volume_decay=True,
          cluster_status="isolated", stretch_sma50_atr=-2.0):
    def f(v, unit="x"):
        return {"v": v, "unit": unit, "asof": "2026-01-01", "src": "test"}
    return {
        "P1.chg_pct_1d": f(chg, "pct"),
        "P2.atr14": f(atr14, "USD"),
        "P9.stretch_sma50_atr": f(stretch_sma50_atr, "ATRs"),
        "P9.rsi_percentile_conditional": f(conditional_pctile, "pctile"),
        "P9.volume_decay_flag": f(volume_decay, "bool"),
        "P9.cluster_status": f(cluster_status, "label"),
    }


def test_build_raises_on_missing_required_facts():
    try:
        mod.build({}, _bars([100.0] * 70))
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_build_raises_on_short_history():
    try:
        mod.build(_pack(-13.9), _bars([100.0] * 10))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_all_four_conditions_met_oversold_case():
    # 65 flat bars establishing a stable RSI baseline, then a sharp dip and a
    # clean 3-day higher-closes bounce with no new ATR-magnitude down day.
    closes = [100.0] * 65 + [90.0, 88.0, 92.0, 95.0, 98.0]
    pack = _pack(chg=(98.0 / 95.0 - 1) * 100, atr14=2.0, conditional_pctile=5.0,
                 volume_decay=True, cluster_status="isolated")
    facts = mod.build(pack, _bars(closes))
    assert facts["P9.exhaustion_direction"]["v"] == "oversold_turning_up"
    assert facts["P9.exhaustion_vol_decay"]["v"] is True
    assert facts["P9.exhaustion_higher_closes"]["v"] is True
    assert facts["P9.exhaustion_crashfree_window"]["v"] is True  # isolated -> vacuous
    k = facts["P9.exhaustion_tally_k"]["v"]
    assert facts["P9.exhaustion_tally"]["v"] == f"{k}/4"
    assert k >= 2  # at least vol_decay + higher_closes + crashfree


def test_build_zero_conditions_when_nothing_confirms():
    closes = [100.0] * 65 + [90.0, 88.0, 86.0, 85.0, 84.0]  # still falling, no bounce
    pack = _pack(chg=(84.0 / 85.0 - 1) * 100, atr14=2.0, conditional_pctile=50.0,
                 volume_decay=False, cluster_status="isolated")
    facts = mod.build(pack, _bars(closes))
    assert facts["P9.exhaustion_rsi_turn"]["v"] is False
    assert facts["P9.exhaustion_vol_decay"]["v"] is False
    assert facts["P9.exhaustion_higher_closes"]["v"] is False
    assert facts["P9.exhaustion_tally_k"]["v"] == 1  # crashfree vacuously true (isolated)
    assert facts["P9.exhaustion_tally"]["v"] == "1/4"


def test_main_cli_smoke(tmp_path, capsys):
    closes = [100.0] * 65 + [90.0, 88.0, 92.0, 95.0, 98.0]
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(_pack(chg=3.0, atr14=2.0)))
    h.write_text(json.dumps(_bars(closes)))
    code = mod.main([str(p), str(h)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "P9.exhaustion_tally" in out
