"""tests for scripts/distillers/_base.py (Step 1 of the distilled-signal-pack v1
slice): signal() optional-key omission, merge_signals() R3/R4/R5 rule set."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from distillers import signal, DistillCtx, merge_signals  # noqa: E402


def test_signal_omits_none_optionals():
    s = signal("P0.move_pct", 1.5, "pct", "2026-07-06", "schwab:bars")
    assert s == {
        "id": "P0.move_pct",
        "v": 1.5,
        "unit": "pct",
        "asof": "2026-07-06",
        "src": "schwab:bars",
    }
    assert "rank" not in s
    assert "notable" not in s
    assert "gap" not in s


def test_signal_keeps_provided_optionals():
    s = signal(
        "P8.gex_series",
        [1, 2, 3],
        "list",
        "2026-07-06",
        "uw:options",
        rank=1,
        notable=True,
        gap="22 rows omitted",
    )
    assert s["rank"] == 1
    assert s["notable"] is True
    assert s["gap"] == "22 rows omitted"


def test_distillctx_frozen_dataclass_fields():
    ctx = DistillCtx(
        ticker="MRVL",
        kind="equity",
        asof="2026-07-06",
        mode="live",
        facts={},
        spot=100.0,
        atr=2.0,
        max_rows=8,
        max_tokens=None,
        entry=None,
    )
    assert ctx.ticker == "MRVL"
    assert ctx.mode == "live"
    assert ctx.max_rows == 8
    try:
        ctx.ticker = "AAPL"
        assert False, "DistillCtx must be frozen"
    except Exception:
        pass


def test_merge_pure_omission_routes_to_gaps_only():
    facts, gaps = {}, []
    signals = [
        {"id": "P0.rel_volume", "v": None, "unit": "ratio", "asof": "2026-07-06",
         "src": "schwab:bars", "gap": "day_volume unavailable in replay"},
    ]
    merge_signals(facts, gaps, signals, "schwab:bars")
    assert "P0.rel_volume" not in facts
    assert gaps == ["P0.rel_volume day_volume unavailable in replay"]


def test_merge_keeps_capped_fact_and_names_omission():
    facts, gaps = {}, []
    signals = [
        {"id": "P8.gex_series", "v": [1, 2, 3], "unit": "list", "asof": "2026-07-06",
         "src": "uw:options", "gap": "22 of 30 rows omitted"},
    ]
    merge_signals(facts, gaps, signals, "uw:options")
    assert facts["P8.gex_series"]["v"] == [1, 2, 3]
    assert gaps == ["P8.gex_series (capped): 22 of 30 rows omitted"]


def test_merge_backfills_src_from_cite_src():
    facts, gaps = {}, []
    signals = [
        {"id": "P0.move_pct", "v": 1.5, "unit": "pct", "asof": "2026-07-06"},
    ]
    merge_signals(facts, gaps, signals, "schwab:bars")
    assert facts["P0.move_pct"]["src"] == "schwab:bars"


def test_merge_preserves_explicit_src_over_cite_src():
    facts, gaps = {}, []
    signals = [
        {"id": "P0.move_pct", "v": 1.5, "unit": "pct", "asof": "2026-07-06",
         "src": "schwab:quote"},
    ]
    merge_signals(facts, gaps, signals, "schwab:bars")
    assert facts["P0.move_pct"]["src"] == "schwab:quote"


def test_merge_extra_keys_survive_into_stored_fact():
    facts, gaps = {}, []
    signals = [
        {"id": "P0.move_vs_atr", "v": 2.1, "unit": "ratio", "asof": "2026-07-06",
         "src": "schwab:bars", "rank": 1, "notable": True},
    ]
    merge_signals(facts, gaps, signals, "schwab:bars")
    stored = facts["P0.move_vs_atr"]
    assert stored["rank"] == 1
    assert stored["notable"] is True
    assert "id" not in stored


def test_merge_quiet_signal_stored_as_fact_not_gap():
    facts, gaps = {}, []
    signals = [
        {"id": "P0.quiet", "v": "quiet: flat tape, no news", "unit": "none",
         "asof": "2026-07-06", "src": "schwab:bars", "notable": False},
    ]
    merge_signals(facts, gaps, signals, "schwab:bars")
    assert facts["P0.quiet"]["v"] == "quiet: flat tape, no news"
    assert gaps == []
