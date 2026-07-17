import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _movestats as mod


def test_parse_history_prefers_adjclose_ascending():
    hist = {"ticker": "X", "asof": "2026-01-05", "bars": [
        {"date": "2026-01-02", "close": 10.0, "adjClose": 9.0, "volume": 100},
        {"date": "2026-01-01", "close": 9.0, "adjClose": 8.0, "volume": 90},
    ]}
    dates, closes, volumes = mod.parse_history(hist)
    assert dates == ["2026-01-01", "2026-01-02"]
    assert closes == [8.0, 9.0]
    assert volumes == [90.0, 100.0]


def test_daily_returns_pct():
    rets = mod.daily_returns_pct([100.0, 110.0, 99.0])
    assert rets[0] is None
    assert rets[1] == pytest.approx(10.0)
    assert rets[2] == pytest.approx(-10.0)


def test_rsi14_all_gains_is_100_all_losses_is_0():
    up = [100.0 + i for i in range(20)]
    down = [120.0 - i for i in range(20)]
    rsi_up = mod.rsi14(up)
    rsi_down = mod.rsi14(down)
    assert all(v is None for v in rsi_up[:14])
    assert rsi_up[14] == pytest.approx(100.0)
    assert rsi_down[14] == pytest.approx(0.0)


def test_percentile_rank():
    pop = [1, 2, 3, 4, 5]
    assert mod.percentile_rank(3, pop) == pytest.approx(60.0)
    assert mod.percentile_rank(5, pop) == pytest.approx(100.0)
    assert mod.percentile_rank(0, pop) == pytest.approx(0.0)
    assert mod.percentile_rank(1, []) is None


def test_zscore():
    pop = [10.0, 10.0, 10.0, 10.0]
    assert mod.zscore(10.0, pop) == 0.0
    pop2 = [1.0, 2.0, 3.0]
    assert mod.zscore(3.0, pop2) == pytest.approx(1.224744871)
    assert mod.zscore(1.0, []) is None


def test_comparable_event_indices_direction_and_threshold():
    rets = [None, -20.0, 5.0, -15.0, -5.0]
    down = mod.comparable_event_indices(rets, "down", 14.0)
    assert down == [1, 3]
    up = mod.comparable_event_indices(rets, "up", 4.0)
    assert up == [2]


def test_cluster_events_groups_within_window():
    all_dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    events = ["2026-01-02", "2026-01-05", "2026-01-19"]
    clusters = mod.cluster_events(events, all_dates, window_sessions=5)
    assert clusters == [["2026-01-02", "2026-01-05"], ["2026-01-19"]]


def test_macro_cycles_merges_close_clusters_only():
    clusters = [["2020-01-01"], ["2020-02-01"], ["2025-01-01"]]
    macro = mod.macro_cycles(clusters, gap_days=545)
    assert macro == [["2020-01-01", "2020-02-01"], ["2025-01-01"]]


def test_forward_stats_basic():
    closes = [100.0, 90.0, 95.0, 105.0, 99.0, 120.0]
    stats = mod.forward_stats(closes, event_indices=[1], horizons=(2, 4))
    assert stats[2]["n"] == 1
    assert stats[2]["mean"] == pytest.approx((105.0 / 90.0 - 1) * 100)
    assert stats[2]["worst_dd"] == pytest.approx((90.0 / 90.0 - 1) * 100)  # min over closes[1:4]
    assert stats[4]["n"] == 1
