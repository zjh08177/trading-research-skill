"""Smoke tests for render_report.py — the deterministic HTML delivery layer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_report as rr  # noqa: E402


def _pack():
    def f(v):
        return {"v": v, "unit": "USD", "asof": "2026-07-05", "src": "test"}
    return {"P1.price": f(100.0), "P1.day_low": f(97.0), "P1.day_high": f(103.0),
            "P1.chg_pct_1d": f(-2.0), "P1.high_52w": f(150.0), "P1.low_52w": f(60.0),
            "P2.sma20": f(105.0), "P2.sma50": f(110.0), "P2.sma200": f(90.0),
            "P2.atr14": f(4.0), "P2.rsi14": f(45.0), "P2.macd": f(-1.0),
            "P2.macd_signal": f(-1.5), "P2.sigma30": f(2.0),
            "P3.pe_ttm": f(25.0), "P3.revenue_ttm": f(5e9), "P3.revenue_yoy": f(12.0)}


def test_md_to_html_core_blocks():
    md = "# Title\n\n## Section\n\nA **bold** claim [P2.atr14].\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    h = rr.md_to_html(md)
    assert "<h1>" in h and "<h2>" in h
    assert "<strong>bold</strong>" in h
    assert '<span class="tag">[P2.atr14]</span>' in h
    assert "<table>" in h and "<td>1</td>" in h


def test_dashboard_injected_after_blockquote():
    md = "# T\n\n> meta line\n\n## Body\n\ntext\n"
    dash = "<div class='dash'>X</div>"
    body = rr.md_to_html(md, dash)
    assert body.index("</blockquote>") < body.index("dash")


def test_derive_levels_two_sided_and_labeled():
    lv = rr.derive_levels(_pack(), rating="Hold")
    # spot 100: nearest SMA below = SMA200(90); nearest SMA above = SMA20(105)
    assert lv["downside"]["level"] == 90.0 and "Exit" in lv["downside"]["action"]
    assert lv["upside"]["level"] == 105.0 and lv["upside"]["action"] == "Add / buy"
    assert lv["downside"]["atr_dist"] == 2.5 and lv["upside"]["atr_dist"] == 1.25


def test_sell_upside_is_short_invalidation():
    lv = rr.derive_levels(_pack(), rating="Sell")
    assert "re-rate" in lv["upside"]["action"].lower()


def test_parse_levels_marker_overrides():
    md = "risk stuff\nLEVELS: downside=88.5|Sell upside=112.0|Add basis_dn=SMA200 basis_up=SMA50\n"
    lv = rr.parse_levels_marker(md, _pack(), rating="Hold")
    assert lv["downside"]["level"] == 88.5 and lv["downside"]["action"] == "Sell"
    assert lv["upside"]["level"] == 112.0 and lv["upside"]["basis"] == "SMA50"


def test_marker_none_side_filled_from_derive():
    # writer emits a downside but marks upside None -> upside must be filled from derive,
    # never left None (spec E: a Hold/Sell always carries both sides with an action).
    md = "risk\nLEVELS: downside=88.5|Sell upside=0|None basis_dn=SMA200 basis_up=SMA50\n"
    lv = rr.parse_levels_marker(md, _pack(), rating="Hold")
    assert lv["downside"]["level"] == 88.5 and lv["downside"]["action"] == "Sell"
    assert lv["upside"] is not None and lv["upside"]["action"] == "Add / buy"
    assert lv["derived"] is False  # at least one side is writer-emitted


def test_rail_and_panel_render():
    lv = rr.derive_levels(_pack(), rating="Hold")
    rail = rr.decision_rail(_pack(), lv)
    assert "<svg" in rail and rail.count("<svg") == rail.count("</svg>")
    assert "EXIT" in rail and "ADD" in rail and "<rect" in rail
    panel = rr.key_panel(_pack(), {})
    assert "kpanel" in panel and "Price" in panel
