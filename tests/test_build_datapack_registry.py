"""tests/test_build_datapack_registry.py — Step 7 (impl-plan): wiring
apply_registry_distillers into build_facts/build_facts_replay/render_md/main
(tech-solution §5, AC5/AC-F/AC6). run_cli is faked so no subprocess/network
runs; signal_registry's real REGISTRY is used except for the AC-F stub test.
"""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import build_datapack as bd  # noqa: E402
import signal_registry as reg  # noqa: E402
from distillers import signal as _signal  # noqa: E402


def _fct(v, unit="x", asof="2026-07-06", src="test"):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def _fake_run_cli(returns):
    calls = []

    def run_cli(name, args):
        calls.append(name)
        return returns.get(name, (1, None, "no fake"))
    return run_cli, calls


# Notable move (>=3.0%, >=1.0 ATR) so p0_catcher keeps its real signals instead
# of collapsing to a single P0.quiet line.
# P1/P2 now come from UW; the schwab.fundamental T1 distiller is dormant
# (default_on=False) after the sunset, so its Schwab CLI is never invoked.
BASE = {
    "uw_bars": (0, {"P1.price": _fct(420.0),
                    "P1.chg_pct_1d": _fct(4.5, "pct"),
                    "P2.atr14": _fct(12.5),
                    "P2.atr14_pct": _fct(1.5, "pct")}, ""),
    "uw_quote": (0, {"P1.last": _fct(421.0),
                     "P1.day_volume": _fct(1_000_000, "shares"),
                     "P1.avg_vol_20d": _fct(900_000, "shares")}, ""),
    "tiingo_oracle": (0, {}, ""),
    "edgar_fundamentals": (0, {}, ""),
    "marketaux_news": (0, {"P5.headlines": {"v": [], "unit": "list",
                                            "asof": "2026-07-06", "src": "marketaux"}}, ""),
    # Present but never consumed: schwab.fundamental is dormant post-sunset.
    "schwab_fundamental": (0, {"_schwab_fundamental": {"beta": 1.8}}, ""),
}


def test_live_build_facts_p0_present_fundamental_dormant(monkeypatch):
    fake, calls = _fake_run_cli(BASE)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, degraded, xline = bd.build_facts("MRVL", "equity", options=False)
    p0_keys = [k for k in facts if k.startswith("P0.")]
    assert p0_keys, "P0 catcher must contribute at least one fact"
    assert "P0.move_pct" in facts and facts["P0.move_pct"]["v"] == 4.5
    # Schwab sunset: the fundamental distiller is dormant -> beta not fetched.
    assert "P3.beta" not in facts
    assert "schwab_fundamental" not in calls


def test_live_t2_caps_p8_list_when_options(monkeypatch):
    rows = [["call", 100, "2026-08-21", i] for i in range(20)]
    rets = {**BASE, "uw_options": (0, {
        "P8.gex_net": _fct(8e8, "usd"),
        "P8.flow_alerts": {"v": rows, "unit": "list", "asof": "2026-07-06", "src": "uw"},
    }, "")}
    fake, calls = _fake_run_cli(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, degraded, xline = bd.build_facts("MRVL", "equity", options=True)
    kept = facts["P8.flow_alerts"]["v"]
    assert len(kept) <= 8  # uw.options_depth registry entry max_rows=8
    assert any("P8.flow_alerts" in g and "capped" in g for g in gaps)
    assert "P8.gex_net" in facts  # scalar preserved, un-capped


def test_lean_options_suppresses_raw_p8_uncapped(monkeypatch):
    # BLOCKER regression (code-judge #1): --profile lean drops the tier-2
    # uw.options_depth capper; raw P8 must NOT be fetched/emitted uncapped —
    # fetch is bound to the presence of its capping distiller (R1/R3/AC1).
    rows = [["call", 100, "2026-08-21", i] for i in range(20)]
    rets = {**BASE, "uw_options": (0, {
        "P8.gex_net": _fct(8e8, "usd"),
        "P8.flow_alerts": {"v": rows, "unit": "list", "asof": "2026-07-06", "src": "uw"},
    }, "")}
    fake, calls = _fake_run_cli(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    monkeypatch.setattr(bd, "PROFILE", "lean")
    facts, gaps, degraded, xline = bd.build_facts("MRVL", "equity", options=True)
    assert not any(k.startswith("P8.") for k in facts), "lean must ship no raw P8 fact"
    assert "uw_options" not in calls, "lean must not even fetch UW P8"
    assert any("P8 omitted (profile=lean" in g for g in gaps), "omission must be named"


def test_replay_omits_t1_t2_named_and_p0_within_cutoff(monkeypatch):
    fake, calls = _fake_run_cli(BASE)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, degraded, xline, eff = bd.build_facts_replay("MRVL", "equity", "2026-07-06")
    assert "P3.beta" not in facts
    assert not any(k.startswith("P8.") for k in facts)
    assert "schwab_fundamental" not in calls  # dormant post-sunset; never fetched
    # schwab.fundamental is default_on=False, so it is absent from the live
    # footprint too -> it is NOT a replay-only omission. uw.options_depth still is.
    assert not any("schwab.fundamental omitted in replay" in g for g in gaps)
    assert any("uw.options_depth omitted in replay (live-only)" in g for g in gaps)
    p0_keys = [k for k in facts if k.startswith("P0.")]
    assert p0_keys
    for k in p0_keys:
        assert str(facts[k]["asof"])[:10] <= "2026-07-06"


def test_ac_f_stub_feed_no_control_flow_edit():
    stub = reg.FeedEntry(
        feed_id="stub.feed", section="P9", tier=1, vendor="derived",
        endpoint="derived", cost="free", cadence="per-run",
        replay_safe=True, default_on=True, max_rows=0, max_tokens=None,
        source="derive",
        distiller=lambda raw, ctx: [_signal("P9.stub", 1, "count", ctx.asof, "derived")],
        cite_src="derived",
    )
    facts, gaps, degraded = {}, [], []
    ctx_base = {"ticker": "X", "kind": "equity", "asof": "2026-07-06", "mode": "live"}
    bd.apply_registry_distillers(facts, gaps, degraded, ctx_base,
                                  mode="live", profile="full", options=False,
                                  registry=[stub])
    assert facts["P9.stub"]["v"] == 1


def test_missing_holdings_fails_closed(tmp_path):
    """A missing book is NOT an empty book. This used to return {"holdings": []},
    which silently dropped the position section from every report — an unreadable
    file and a genuinely flat account produced identical output. It now exits
    non-zero; only an explicit empty book runs flat."""
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(SystemExit) as e:
        bd._load_holdings(str(missing))
    assert e.value.code != 0


def test_explicit_empty_book_still_runs_flat(tmp_path):
    """The escape hatch the error message promises has to actually work."""
    empty = tmp_path / "empty.json"
    empty.write_text('{"holdings": []}')
    assert bd._load_holdings(str(empty)) == {"holdings": []}


def test_render_md_p0_before_p1():
    facts = {"P1.last": _fct(421.0, "USD"), "P0.move_pct": _fct(4.5, "pct")}
    md, run_id = bd.render_md("MRVL", "equity", facts, ["none"], [], "x-check", "no record")
    assert "## P0 What-changed" in md
    assert md.index("## P0 What-changed") < md.index("## P1 Quote")


def test_render_md_p8_still_before_p7_after_p0_add():
    facts = {"P8.gex_net": _fct(8e8, "usd"), "P0.move_pct": _fct(4.5, "pct")}
    md, run_id = bd.render_md("MRVL", "equity", facts, ["none"], [], "x-check", "no record")
    assert md.index("## P8 Dealer") < md.index("## P7 Track record")
