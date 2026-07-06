"""build_datapack --options wiring (A8): UW P8 primary, Schwab P4 gated on P8
failure, P8._gaps routed, P8 section rendered before P7. run_cli is faked so no
subprocess/network runs."""
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
    "schwab_bars": (0, {"P1.price": _fct(420.0), "P2.atr14": _fct(12.5)}, ""),
    "schwab_quote": (0, {"P1.last": _fct(421.0)}, ""),
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
    assert "schwab_options" not in calls                    # IV present -> P4 fully suppressed
    assert any("P4 suppressed under --options" in g for g in gaps)
    assert any("DATA-THIN(flow)" in g for g in gaps)        # P8._gaps routed
    assert "P8._gaps" not in facts                          # popped, not a fact


def test_p8_success_missing_iv_backfills_only_iv(monkeypatch):
    # EC4/D2: P8 succeeds but the IV group gapped -> backfill ONLY Schwab
    # P4.atm_iv_near, leave other P4 fields suppressed.
    rets = {**BASE, "uw_options": (0, {"P8.gex_net": _fct(8e8, "usd")}, ""),
            "schwab_options": (0, {"P4.atm_iv_near": _fct(0.45, "ratio"),
                                   "P4.put_call_volume_ratio": _fct(0.8, "ratio")}, "")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=True)
    assert "schwab_options" in calls
    assert "P4.atm_iv_near" in facts                        # IV backfilled
    assert "P4.put_call_volume_ratio" not in facts          # other P4 fields stay suppressed
    assert any("backfilled from Schwab" in g for g in gaps)


def test_p8_failure_falls_back_to_schwab_p4(monkeypatch):
    rets = {**BASE, "uw_options": (3, None, "tier gate"),
            "schwab_options": (0, {"P4.atm_iv_near": _fct(0.45, "ratio")}, "")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=True)
    assert "schwab_options" in calls                        # P4 fallback fired
    assert "P4.atm_iv_near" in facts
    assert any("uw_options exit 3" in g for g in gaps)


def test_flag_off_uses_schwab_p4_only(monkeypatch):
    rets = {**BASE, "schwab_options": (0, {"P4.atm_iv_near": _fct(0.45, "ratio")}, "")}
    fake, calls = _fake(rets)
    monkeypatch.setattr(bd, "run_cli", fake)
    facts, gaps, _, _ = bd.build_facts("TSLA", "equity", options=False)
    assert "uw_options" not in calls
    assert "P4.atm_iv_near" in facts


def test_render_md_p8_section_before_p7(monkeypatch):
    facts = {"P1.last": _fct(421.0, "USD"), "P8.gex_net": _fct(8e8, "usd"),
             "P8.gex_series": {"v": [["2026-07-02", 8e8]], "unit": "list",
                               "asof": "2026-07-06", "src": "uw"}}
    md, run_id = bd.render_md("TSLA", "equity", facts, ["none"], [], "x-check", "no record")
    assert "## P8 Dealer positioning & options (UW)" in md
    assert md.index("## P8 Dealer") < md.index("## P7 Track record")
