"""tests for scripts/distillers/uw_options_depth.py (Step 4 of the
distilled-signal-pack v1 slice): T2 UW options-depth distillation with
salience caps (R8, tech-solution §4.3). `raw_rows` here is the P8.* fact
dict already built by scripts/vendors/uw_options.build() -- never raw UW
vendor JSON (R1)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from distillers import DistillCtx  # noqa: E402
from distillers import uw_options_depth as D  # noqa: E402


def _ctx(max_rows=8, asof="2026-07-10"):
    return DistillCtx(
        ticker="MRVL",
        kind="equity",
        asof=asof,
        mode="live",
        facts={},
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


def _fact(v, unit, asof="2026-07-10", src="uw", **extra):
    d = {"v": v, "unit": unit, "asof": asof, "src": src}
    d.update(extra)
    return d


def test_flow_direction_bearish_fixture():
    raw = {
        "P8.flow_alerts": _fact(
            [["put_sweep", 100, "2026-07-10", 5000],
             ["call_sweep", 105, "2026-07-10", 1000]],
            "list",
        ),
    }
    out = D.distill(raw, _ctx())
    fd = _by_id(out, "P8.flow_direction")
    assert fd is not None
    assert fd["v"] == "bearish"


def test_flow_direction_bullish_fixture():
    raw = {
        "P8.flow_alerts": _fact(
            [["call_sweep", 100, "2026-07-10", 9000],
             ["put_sweep", 95, "2026-07-10", 500]],
            "list",
        ),
    }
    out = D.distill(raw, _ctx())
    fd = _by_id(out, "P8.flow_direction")
    assert fd["v"] == "bullish"


def test_gex_series_30_rows_capped_with_trend_and_omission_ac_c():
    rows = [[f"2026-06-{i+1:02d}", 1000.0 * i] for i in range(30)]
    raw = {"P8.gex_series": _fact(rows, "list")}
    ctx = _ctx(max_rows=8)
    out = D.distill(raw, ctx)

    capped = _by_id(out, "P8.gex_series")
    assert capped is not None
    assert len(capped["v"]) == 8
    assert capped["gap"] and "8 of 30" in capped["gap"]

    trend = _by_id(out, "P8.gex_series_trend")
    assert trend is not None
    assert trend["v"] == "rising"  # derived from the FULL 30-row series


def test_gex_series_falling_trend():
    rows = [[f"2026-06-{i+1:02d}", 1000.0 * (30 - i)] for i in range(30)]
    raw = {"P8.gex_series": _fact(rows, "list")}
    out = D.distill(raw, _ctx(max_rows=8))
    trend = _by_id(out, "P8.gex_series_trend")
    assert trend["v"] == "falling"


def test_scalars_preserved():
    raw = {
        "P8.gex_net": _fact(800000.0, "usd"),
        "P8.gex_regime": _fact("long-gamma", "label"),
        "P8.iv_rank_1y": _fact(62.5, "pct"),
        "P8.flip_level": _fact(82.5, "price"),
        "P8.dist_flip": _fact(0.15, "ratio"),
        "P8.rr_skew_25d": _fact(-0.03, "ratio", label="put-skewed"),
    }
    out = D.distill(raw, _ctx())
    for fid in ("P8.gex_net", "P8.gex_regime", "P8.iv_rank_1y",
                "P8.flip_level", "P8.dist_flip", "P8.rr_skew_25d"):
        sig = _by_id(out, fid)
        assert sig is not None, fid
        assert sig["v"] == raw[fid]["v"]
        assert sig["unit"] == raw[fid]["unit"]
        assert sig["src"] == "uw"


def test_empty_raw_rows_is_quiet():
    out = D.distill({}, _ctx())
    assert len(out) == 1
    assert out[0]["id"] == "P8.options_quiet"
    assert out[0]["notable"] is False
    assert "quiet" in out[0]["v"]


def test_upstream_gaps_forwarded_when_degenerate():
    out = D.distill({"P8._gaps": ["MISSING(gex_net: HTTP 500 boom)"]}, _ctx())
    quiet = _by_id(out, "P8.options_quiet")
    assert quiet is not None
    gap_signals = [s for s in out if s["id"] == "P8._gaps"]
    assert len(gap_signals) == 1
    assert "boom" in gap_signals[0]["gap"]
    assert gap_signals[0]["v"] is None


def test_no_raw_full_list_survives():
    rows30 = [[f"2026-06-{i+1:02d}", float(i)] for i in range(30)]
    rows12 = [[f"2026-07-{i+1:02d}", i, i * 10] for i in range(12)]
    raw = {
        "P8.gex_series": _fact(rows30, "list"),
        "P8.oi_walls": _fact(rows12, "list"),
        "P8.iv_term": _fact(rows12, "list"),
        "P8.max_pain_by_expiry": _fact(rows12, "list"),
        "P8.gex_by_strike": _fact(rows12, "list"),
        "P8.flow_alerts": _fact(rows12, "list"),
    }
    ctx = _ctx(max_rows=8)
    out = D.distill(raw, ctx)
    for sig in out:
        if sig.get("unit") == "list":
            assert len(sig["v"]) <= 8, sig["id"]


def test_oi_change_agg_from_oi_walls():
    raw = {
        "P8.oi_walls": _fact(
            [["2026-07-17", 1000, 200], ["2026-08-21", 500, 300]], "list",
        ),
    }
    out = D.distill(raw, _ctx())
    agg = _by_id(out, "P8.oi_change_agg")
    assert agg is not None
    assert agg["v"] == 500.0  # 200 + 300
