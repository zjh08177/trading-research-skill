"""build_datapack --options wiring (A8): UW P8 is the SOLE options source after
the Schwab sunset. Without --options there is no light options fact; a P8 failure
or gapped P8 IV group is accepted as a named gap (never a Schwab P4 fallback).
run_cli is faked so no subprocess/network runs."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import build_datapack as bd  # noqa: E402


def _fct(v, unit="x"):
    return {"v": v, "unit": unit, "asof": "2026-07-06", "src": "test"}


def _fake(returns):
    calls = []

    def run_cli(name, args):
        calls.append(name)
        return returns.get(name, (1, None, "no fake"))
    return run_cli, calls


BASE = {
    "uw_bars": (0, {"P1.price": _fct(420.0), "P2.atr14": _fct(12.5)}, ""),
    "uw_quote": (0, {"P1.last": _fct(421.0)}, ""),
    "tiingo_oracle": (0, {}, ""),
    "edgar_fundamentals": (0, {}, ""),
    "marketaux_news": (0, {"P5.headlines": {"v": [], "unit": "list",
                                            "asof": "2026-07-06", "src": "marketaux"}}, ""),
}


def test_p8_success_with_iv_suppresses_p4_and_routes_gaps(monkeypatch):
    rets = {**BASE, "uw_options": (0, {
        "P8.gex_net": _fct(8e8, "usd"), "P8.gex_regime": _fct("long-gamma", "label"),
        "P8.iv_rank_1y": _fct(42.5, "pct"),
        "P8._gaps": ["DATA-THIN(flow): session pre-open"]}, "")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, degraded, xline = bd.build_facts("TSLA", "equity", options=True)
    assert "P8.gex_net" in facts and "P8.gex_regime" in facts
    assert "schwab_options" not in calls                     # Schwab is sunset — never called
    assert any("P4 suppressed under --options" in g for g in gaps)
    assert any("DATA-THIN(flow)" in g for g in gaps)         # P8._gaps routed
    assert "P8._gaps" not in facts                           # popped, not a fact


def test_p8_success_missing_iv_accepts_gap_no_backfill(monkeypatch):
    # After the Schwab sunset: P8 succeeds but the IV group gapped -> accept a
    # named P4 gap. No Schwab P4 backfill; no schwab_options call.
    rets = {**BASE, "uw_options": (0, {"P8.gex_net": _fct(8e8, "usd")}, "")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=True)
    assert "schwab_options" not in calls
    assert "P4.atm_iv_near" not in facts                     # no Schwab backfill
    assert any("P8 IV group gapped; no IV backfill" in g for g in gaps)


def test_p8_failure_accepts_gap_no_schwab_fallback(monkeypatch):
    rets = {**BASE, "uw_options": (3, None, "tier gate")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=True)
    assert "schwab_options" not in calls                     # Schwab is sunset
    assert "P4.atm_iv_near" not in facts
    assert any("uw_options exit 3" in g and "no options data" in g for g in gaps)


def test_flag_off_yields_named_p4_gap(monkeypatch):
    fake, calls = _fake(dict(BASE))
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=False)
    assert "uw_options" not in calls
    assert "schwab_options" not in calls
    assert "P4.atm_iv_near" not in facts
    assert any("no light options source after Schwab sunset" in g for g in gaps)


def test_render_md_p8_section_before_p7(monkeypatch):
    facts = {"P1.last": _fct(421.0, "USD"), "P8.gex_net": _fct(8e8, "usd"),
             "P8.gex_series": {"v": [["2026-07-02", 8e8]], "unit": "list",
                               "asof": "2026-07-06", "src": "uw"}}
    md, run_id = bd.render_md("TSLA", "equity", facts, ["none"], [], "x-check", "no record")
    assert "## P8 Dealer positioning & options (UW)" in md
    assert md.index("## P8 Dealer") < md.index("## P7 Track record")
