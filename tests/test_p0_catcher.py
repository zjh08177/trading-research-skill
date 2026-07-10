"""tests for scripts/distillers/p0_catcher.py (Step 3 of the distilled-signal-pack
v1 slice): tape-move notable, catalyst cap+aggregate+omission, quiet, replay
degrade (R6/AC1/AC3, tech-solution §4.1)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from distillers import DistillCtx  # noqa: E402
from distillers import p0_catcher  # noqa: E402


def _ctx(facts, mode="live", asof="2026-07-06", max_rows=3):
    return DistillCtx(
        ticker="MRVL",
        kind="equity",
        asof=asof,
        mode=mode,
        facts=facts,
        spot=100.0,
        atr=2.0,
        max_rows=max_rows,
        max_tokens=None,
        entry=None,
    )


def _by_id(signals, sid):
    for s in signals:
        if s["id"] == sid:
            return s
    return None


def test_notable_tape_move_flagged():
    facts = {
        "P1.chg_pct_1d": {"v": 5.0, "unit": "pct", "asof": "2026-07-06", "src": "schwab:bars"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06", "src": "schwab:bars"},
    }
    ctx = _ctx(facts)
    out = p0_catcher.distill(None, ctx)
    move = _by_id(out, "P0.move_pct")
    atr = _by_id(out, "P0.move_vs_atr")
    assert move["v"] == 5.0
    assert move["notable"] is True
    assert atr["v"] == 2.5
    assert atr["notable"] is True
    assert _by_id(out, "P0.quiet") is None


def test_catalyst_cap_aggregate_and_omission_ac1():
    headlines = [
        {"title": f"story {i}", "published_at": f"2026-07-0{i}", "sentiment": 0.1}
        for i in range(1, 6)
    ]
    facts = {
        "P1.chg_pct_1d": {"v": 0.1, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
        "P5.headlines": {"v": headlines, "unit": "list", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts, max_rows=3)
    out = p0_catcher.distill(None, ctx)
    catalysts = _by_id(out, "P0.catalysts")
    count = _by_id(out, "P0.catalyst_count")
    assert catalysts is not None
    assert len(catalysts["v"]) == 3
    assert "kept top 3 of 5" in catalysts["gap"]
    assert count is not None
    assert count["v"] == 5
    assert "gap" not in count


def test_quiet_when_flat_and_no_news_ac3():
    facts = {
        "P1.chg_pct_1d": {"v": 0.1, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts)
    out = p0_catcher.distill(None, ctx)
    assert len(out) == 1
    assert out[0]["id"] == "P0.quiet"
    assert out[0]["notable"] is False
    assert "quiet" in out[0]["v"]


def test_replay_mode_rel_volume_named_gap_and_asof_le_cutoff():
    cutoff = "2026-07-06"
    facts = {
        "P1.chg_pct_1d": {"v": 5.0, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
        "P5.headlines": {"v": [
            {"title": "breaking", "published_at": "2026-07-05", "sentiment": 0.2},
        ], "unit": "list", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts, mode="replay", asof=cutoff)
    out = p0_catcher.distill(None, ctx)
    relvol = _by_id(out, "P0.rel_volume")
    assert relvol["v"] is None
    assert "replay" in relvol["gap"]
    move = _by_id(out, "P0.move_pct")
    catalysts = _by_id(out, "P0.catalysts")
    assert move["asof"] <= cutoff
    assert catalysts["asof"] <= cutoff


def test_days_to_earnings_notable_within_7d():
    facts = {
        "P1.chg_pct_1d": {"v": 5.0, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
        "P5.next_earnings": {"v": "2026-07-10", "unit": "date", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts, asof="2026-07-06")
    out = p0_catcher.distill(None, ctx)
    earn = _by_id(out, "P0.days_to_earnings")
    assert earn["v"] == 4
    assert earn["notable"] is True


def test_days_to_earnings_live_only_gap_in_replay():
    facts = {
        "P1.chg_pct_1d": {"v": 5.0, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
        "P5.next_earnings": {"v": "2026-07-10", "unit": "date", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts, mode="replay", asof="2026-07-06")
    out = p0_catcher.distill(None, ctx)
    earn = _by_id(out, "P0.days_to_earnings")
    assert earn["v"] is None
    assert "replay" in earn["gap"]


def test_gap_pct_and_8k_are_always_named_gaps():
    facts = {
        "P1.chg_pct_1d": {"v": 5.0, "unit": "pct", "asof": "2026-07-06"},
        "P2.atr14_pct": {"v": 2.0, "unit": "pct", "asof": "2026-07-06"},
    }
    ctx = _ctx(facts)
    out = p0_catcher.distill(None, ctx)
    gap_pct = _by_id(out, "P0.gap_pct")
    k8 = _by_id(out, "P0.catalyst_8k")
    assert gap_pct["v"] is None and gap_pct["gap"]
    assert k8["v"] is None and k8["gap"]


def test_null_tolerant_no_crash_on_missing_facts():
    ctx = _ctx({})
    out = p0_catcher.distill(None, ctx)
    # must not raise, and with nothing present it degrades to quiet
    assert isinstance(out, list)
    assert out[0]["id"] == "P0.quiet"
