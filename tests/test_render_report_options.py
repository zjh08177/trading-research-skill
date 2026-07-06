"""render_report P8 key-panel tiles (A9): the Options group leads with UW P8
(net GEX + regime, IV rank, gamma flip, skew) when present, and falls back to the
Schwab P4 IV tiles when P8 is absent (so a P4-suppressed --options run doesn't
blank the panel)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import render_report as rr  # noqa: E402


def _f(v, unit="x", **e):
    return {"v": v, "unit": unit, "asof": "2026-07-06", "src": "uw", **e}


def test_p8_tiles_render_when_present():
    pack = {
        "P1.price": _f(420.0, "USD"), "P2.atr14": _f(12.5, "USD"),
        "P8.gex_net": _f(8.1e8, "usd"), "P8.gex_regime": _f("long-gamma", "label"),
        "P8.iv_rank_1y": _f(42.5, "pct"), "P8.flip_level": _f(418.75, "price"),
        "P8.dist_flip": _f(0.0061, "ratio"),
        "P8.rr_skew_25d": _f(-0.035, "ratio", label="put-skewed"),
    }
    html = rr.key_panel(pack, {})
    assert "Net GEX" in html and "long-gamma" in html
    assert "IV rank 1y" in html and "Gamma flip" in html
    assert "put-skewed" in html
    assert "Options positioning" in html


def test_p4_fallback_when_no_p8():
    pack = {"P1.price": _f(420.0, "USD"), "P2.atr14": _f(12.5, "USD"),
            "P4.atm_iv_near": _f(0.45, "ratio")}
    html = rr.key_panel(pack, {})
    assert "ATM IV" in html and "Net GEX" not in html


def test_no_options_group_when_neither():
    pack = {"P1.price": _f(420.0, "USD"), "P2.atr14": _f(12.5, "USD")}
    html = rr.key_panel(pack, {})
    assert "Options positioning" not in html
