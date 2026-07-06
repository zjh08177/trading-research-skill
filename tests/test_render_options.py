"""render_options P8 block tests. Drives build() on a pack shaped like
uw_options' emit, and cross-verifies every tagged number through
qa_check.check_pairs (EC3 trace / EC5 history tags / EC6 unit handling)."""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import render_options as R  # noqa: E402
import qa_check as qa  # noqa: E402


def _fact(v, unit, history, **extra):
    return {"v": v, "unit": unit, "asof": "2026-07-06", "src": "uw",
            "history": history, "derived": False, **extra}


def _pack():
    return {
        "P8.gex_regime": _fact("long-gamma", "label", "snapshot", derived=True),
        "P8.gex_net": _fact(812000000.0, "usd", "daily", derived=True),
        "P8.gex_front_dte": _fact(-45000000.0, "usd", "snapshot", derived=True),
        "P8.flip_level": _fact(418.75, "price", "snapshot", derived=True),
        "P8.dist_flip": _fact(0.0612, "ratio", "snapshot", derived=True),
        "P8.iv_rank_1y": _fact(42.5, "pct", "snapshot"),
        "P8.iv_now": _fact(0.4512, "ratio", "daily"),
        "P8.rv_now": _fact(0.3821, "ratio", "daily"),
        "P8.implied_move_front": _fact(0.0725, "ratio", "snapshot", event="event-inclusive"),
        "P8.rr_skew_25d": _fact(-0.035, "ratio", "daily", label="put-skewed"),
        "P8.max_pain_front": _fact(430.0, "price", "snapshot"),
        "P8.call_wall": _fact(450.0, "price", "snapshot"),
        "P8.dist_call_wall": _fact(0.0743, "ratio", "snapshot", derived=True),
        "P8.put_wall": _fact(400.0, "price", "snapshot"),
        "P8.dist_put_wall": _fact(-0.0446, "ratio", "snapshot", derived=True),
        "P8.net_prem_day": _fact(15400000.0, "usd", "daily"),
        "P8.pc_ratio_vol": _fact(0.82, "ratio", "daily"),
        "P8.net_prem_ticks": _fact(2200000.0, "usd", "live", session_state="mid"),
        "P8.nope": _fact(-0.12, "ratio", "live", session_state="mid"),
        "P8.gex_series": _fact([["2026-07-01", 700000000.0], ["2026-07-02", 812000000.0]],
                               "list", "daily"),
        "P8.gex_by_strike": _fact([[450.0, 90000000.0], [400.0, -70000000.0]], "list", "snapshot"),
        "P8.flow_alerts": _fact([["sweep", 420, "2026-07-10", 1200]], "list", "live",
                                session_state="mid"),
    }


def test_verbatim_markers_present():
    block = R.build(_pack())
    assert block.startswith("<!-- options-block: inserted verbatim, do not edit -->")
    assert block.rstrip().endswith("<!-- options-block: end -->")


def test_every_tagged_number_verifies_against_pack():
    # EC3/EC6: check_pairs must PASS on every rendered scalar; the %+ratio -> /100
    # rule must fire for IV/dist/skew shown as percents.
    pack = _pack()
    block = R.build(pack)
    results = qa.check_pairs(block, pack)
    fails = [m for ok, m in results if not ok]
    assert not fails, fails
    assert results, "expected at least one tagged pair"


def test_history_tags_rendered():
    # EC5: each fact carries its daily/snapshot/live tag.
    block = R.build(_pack())
    assert "[P8.gex_net]" in block and "(daily" in block
    assert "[P8.flip_level]" in block and "(snapshot" in block
    assert "[P8.net_prem_ticks]" in block and "session=mid" in block


def test_ratio_shown_as_percent_and_raw():
    block = R.build(_pack())
    assert "45.12% [P8.iv_now]" in block            # ratio -> percent
    assert "-3.50% [P8.rr_skew_25d]" in block       # negative skew percent
    assert "0.8200 [P8.pc_ratio_vol]" in block      # genuine ratio -> raw


def test_regime_and_event_and_skew_labels():
    block = R.build(_pack())
    assert "long-gamma" in block and "[P8.gex_regime]" in block
    assert "event-inclusive" in block               # O5 event tag
    assert "put-skewed" in block                     # O6 skew direction


def test_context_lists_are_tables_cited_by_tag_no_number_tag():
    # A number must never be tagged to a list fact (check_pairs hard-fails those).
    pack = _pack()
    block = R.build(pack)
    assert "[P8.gex_series]" in block and "| Date | Net GEX |" in block
    # no "non-scalar fact numerically tagged" failure:
    assert not any("non-scalar" in m for ok, m in qa.check_pairs(block, pack) if not ok)


def test_short_gamma_flip_line_explicit():
    pack = {"P8.gex_net": _fact(-500000000.0, "usd", "daily", derived=True),
            "P8.gex_regime": _fact("short-gamma", "label", "snapshot", derived=True)}
    block = R.build(pack)
    assert "short-gamma / no net-GEX zero-crossing" in block
    assert "P8.flip_level" not in block


def test_uw_down_renders_named_gap_not_blank():
    # EC4: nothing returns except gaps -> DATA GAP line + gap list, no crash.
    pack = {"P8._gaps": ["MISSING(gex_net: HTTP 401)", "DATA-THIN(dealer)"]}
    block = R.build(pack)
    assert "DATA GAP" in block and "Unusual Whales options data" in block
    assert "MISSING(gex_net: HTTP 401)" in block


def test_empty_pack_raises():
    try:
        R.build({"P1.price": {"v": 1.0}})
    except KeyError:
        return
    assert False, "expected KeyError on a pack with no P8 facts"
