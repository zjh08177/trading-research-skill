"""uw_options P8 formula suite — drives build() through a fake fetch seam
(no network). Covers the tech-solution §4/§8 cases: net GEX sign, nearest-spot
flip (incl. the live-TSLA multimodal case), short-gamma omit, data-inconsistent,
null put_gex, floor→transitional, band→near-flip, skew sign, thin→DATA-THIN."""
import sys
import pathlib

VENDORS = str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vendors")
if VENDORS not in sys.path:
    sys.path.insert(0, VENDORS)

import uw_options as U  # noqa: E402


class FakeFetch:
    """Maps a path substring -> canned payload; records gaps like the real one."""

    def __init__(self, data):
        self.data = data
        self.gaps = []
        self.auth_failed = False
        self.rate_limited = False

    def get(self, path, fact_name, params=None):
        for key, val in self.data.items():
            if key in path:
                return val
        self.gaps.append(f"MISSING({fact_name})")
        return None


def agg(nets):
    """greek-exposure agg rows (oldest-first) from a list of net-GEX values."""
    return [{"date": f"2026-06-{i+1:02d}", "call_gamma": str(n), "put_gamma": "0"} for i, n in enumerate(nets)]


def ladder(pairs):
    """greek-exposure/strike rows from (strike, net_gex) pairs."""
    return [{"date": "2026-07-02", "strike": str(k),
             "call_gex": (str(v) if v is not None else None), "put_gex": "0"} for k, v in pairs]


def build(data, spot, atr=None, earnings=None, ticker="TEST"):
    fetch = FakeFetch(data)
    F, gaps = U.build(ticker, spot, atr, earnings, fetch)
    return F, gaps


def test_net_gex_sign_and_series():
    F, _ = build({"greek-exposure": agg([600000, 700000, 800000])}, spot=100)
    assert F["P8.gex_net"]["v"] == 800000  # last row
    assert F["P8.gex_net"]["history"] == "daily" and F["P8.gex_net"]["derived"] is True
    assert F["P8.gex_series"]["unit"] == "list" and len(F["P8.gex_series"]["v"]) == 3


def test_golden_long_gamma():
    data = {
        "greek-exposure/strike": ladder([(70, -10), (80, 5), (90, 20), (100, 30)]),
        "greek-exposure": agg([600000, 700000, 900000, 850000, 800000]),
    }
    F, _ = build(data, spot=100, atr=10)
    assert abs(F["P8.flip_level"]["v"] - 82.5) < 0.01          # single crossing at 82.5
    assert F["P8.gex_regime"]["v"] == "long-gamma"             # |100-82.5|=17.5 > band 15
    assert F["P8.gex_data_inconsistent"]["v"] is False
    assert F["P8.dist_flip"]["unit"] == "ratio"                # shown as %, so /100 qa rule must fire


def test_multimodal_picks_nearest_spot():
    # live-TSLA shape: crossings at ~95, ~200, ~425; spot 393 -> nearest = 425
    data = {"greek-exposure/strike": ladder([(90, -10), (100, 20), (400, -30), (450, 40)]),
            "greek-exposure": agg([500000, 500000])}
    F, _ = build(data, spot=393, atr=10)
    assert abs(F["P8.flip_level"]["v"] - 425) < 1.0
    assert F["P8.flip_level"]["v"] != 95


def test_short_gamma_no_crossing_omits_flip():
    data = {"greek-exposure/strike": ladder([(80, -10), (90, -20), (100, -5)]),  # cum never crosses 0
            "greek-exposure": agg([-400000, -500000, -600000])}
    F, _ = build(data, spot=95, atr=5)
    assert "P8.flip_level" not in F
    assert F["P8.gex_regime"]["v"] == "short-gamma"


def test_data_inconsistent_flag():
    # gex_net > 0 but the flip sits ABOVE spot (spot < flip) -> inconsistent
    data = {"greek-exposure/strike": ladder([(110, -10), (130, 30)]),  # crossing ~113 > spot 100
            "greek-exposure": agg([500000, 500000])}
    F, _ = build(data, spot=100, atr=5)
    assert F["P8.gex_data_inconsistent"]["v"] is True
    assert F["P8.gex_regime"]["v"] == "long-gamma"  # inconsistent -> flip ignored, sign wins


def test_null_put_gex_no_crash():
    data = {"greek-exposure/strike": ladder([(80, -10), (90, None), (100, 30)]),
            "greek-exposure": agg([500000])}
    F, _ = build(data, spot=95, atr=5)
    assert "P8.gex_by_strike" in F  # built without raising


def test_floor_transitional():
    data = {"greek-exposure": agg([5000, 6000, 7000, 8000, 100])}  # last=100 << p25=5000
    F, _ = build(data, spot=100)
    assert F["P8.gex_regime"]["v"] == "transitional"


def test_band_near_flip():
    data = {"greek-exposure/strike": ladder([(96, -5), (99, 5)]),  # crossing ~97.5 near spot 100
            "greek-exposure": agg([500000, 500000])}
    F, _ = build(data, spot=100, atr=5)  # band = 7.5; |100-97.5|=2.5 <= 7.5
    assert F["P8.gex_regime"]["v"] == "near-flip"


def test_skew_sign():
    put = {"historical-risk-reversal-skew": [{"date": "2026-07-01", "risk_reversal": "-0.035"}]}
    call = {"historical-risk-reversal-skew": [{"date": "2026-07-01", "risk_reversal": "0.02"}]}
    Fp, _ = build(put, spot=100)
    Fc, _ = build(call, spot=100)
    assert Fp["P8.rr_skew_25d"]["v"] < 0 and Fp["P8.rr_skew_25d"]["label"] == "put-skewed"
    assert Fc["P8.rr_skew_25d"]["label"] == "call-skewed"


def test_thin_name_data_thin_no_regime():
    F, gaps = build({}, spot=100)  # nothing returns
    assert "P8.gex_regime" not in F
    assert any("DATA-THIN(dealer)" in g for g in gaps)


def test_event_tag_unknown_without_earnings():
    data = {"volatility/term-structure": [{"date": "2026-07-02", "expiry": "2026-07-10",
                                           "dte": 8, "implied_move_perc": "0.05"}]}
    F, gaps = build(data, spot=100, earnings=None)
    assert F["P8.implied_move_front"]["event"] == "event-status-unknown"
    # O5/EC12: unknown event must ALSO name a gap, not just tag the fact.
    assert any("event-status-unknown" in g for g in gaps)


def test_session_state_stale_on_weekend():
    data = {"stock-state": {"market_time": "r", "tape_time": "2026-07-02T19:59:40Z"}}
    F, _ = build(data, spot=100)  # run_asof is today (> 2026-07-02) -> stale
    assert F["P8.session_state"]["v"] == "none"


def _today():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def test_event_inclusive_when_front_expiry_spans_earnings():
    # EC12 positive: expiry >= earnings >= run_asof -> event-inclusive.
    data = {"volatility/term-structure": [{"date": _today(), "expiry": "2027-01-15",
                                           "dte": 30, "implied_move_perc": "0.06"}]}
    F, _ = build(data, spot=100, earnings="2026-08-01")
    assert F["P8.implied_move_front"]["event"] == "event-inclusive"


def test_session_early_before_1030():
    data = {"stock-state": {"market_time": "r", "tape_time": f"{_today()}T09:31:00Z"}}
    F, _ = build(data, spot=100)
    assert F["P8.session_state"]["v"] == "early"


def test_session_close_after_1530():
    data = {"stock-state": {"market_time": "r", "tape_time": f"{_today()}T15:45:00Z"}}
    F, _ = build(data, spot=100)
    assert F["P8.session_state"]["v"] == "close"


def test_session_pre_open():
    data = {"stock-state": {"market_time": "pm", "tape_time": f"{_today()}T08:00:00Z"}}
    F, _ = build(data, spot=100)
    assert F["P8.session_state"]["v"] == "pre-open"


def test_live_fact_carries_session_stamp():
    # O8: a live fact gates via its session_state stamp.
    data = {"stock-state": {"market_time": "pm", "tape_time": f"{_today()}T08:00:00Z"},
            "nope": [{"date": _today(), "nope": "0.12"}]}
    F, _ = build(data, spot=100)
    assert F["P8.nope"]["history"] == "live"
    assert F["P8.nope"]["session_state"] == "pre-open"


# ---- Smart-money flow scoring (_score_flow / _dte) ----

def _alert(**kw):
    """A flow-alert row with sensible defaults; override any field via kwargs."""
    base = {"type": "call", "strike": "100", "expiry": "2026-09-18",
            "created_at": "2026-07-20T14:00:00Z", "volume": 1000,
            "open_interest": 1000, "total_premium": "600000",
            "total_ask_side_prem": "600000", "total_bid_side_prem": "0",
            "has_sweep": False, "has_multileg": False,
            "all_opening_trades": False, "alert_rule": "SingleHit",
            "volume_oi_ratio": "1.0"}
    base.update(kw)
    return base


def test_dte_calendar_days():
    assert U._dte("2026-07-24", "2026-07-21T10:00:00Z", "2026-07-21") == 3
    assert U._dte("2026-09-18", None, "2026-07-20") == 60
    assert U._dte("bad", "also-bad", "2026-07-20") is None


def test_score_flow_drops_below_premium_floor():
    # Below $300k is ignored entirely (ignore tiny trades).
    rows = U._score_flow([_alert(total_premium="299999")], spot=100, run_asof="2026-07-20")
    assert rows == []


def test_score_flow_premium_tiers():
    big = U._score_flow([_alert(total_premium="1200000")], 100, "2026-07-20")[0]
    mid = U._score_flow([_alert(total_premium="600000")], 100, "2026-07-20")[0]
    assert "very-large-prem" in big[6] and big[0] > mid[0]
    assert "preferred-prem" in mid[6]


def test_score_flow_dte_and_signals():
    # 60-120 DTE + vol>>OI + ask-side + sweep + opening + repeated => high score.
    a = _alert(expiry="2026-09-18", created_at="2026-07-20T14:00:00Z",
               volume=5000, open_interest=300, volume_oi_ratio="16.7",
               has_sweep=True, all_opening_trades=True,
               alert_rule="RepeatedHitsAscendingFill")
    row = U._score_flow([a], 100, "2026-07-20")[0]
    tags = row[6]
    assert "dte-best" in tags and "new-positioning" in tags
    assert "ask-side" in tags and "sweep" in tags
    assert "opening" in tags and "repeated" in tags
    assert row[0] >= 70


def test_score_flow_penalizes_short_dte_and_closing():
    a = _alert(expiry="2026-07-22", created_at="2026-07-21T14:00:00Z",
               volume=100, open_interest=5000, volume_oi_ratio="0.02")
    row = U._score_flow([a], 100, "2026-07-21")[0]
    assert "dte-short" in row[6] and "maybe-closing" in row[6]


def test_score_flow_is_context_list_snapshot():
    data = {"flow-alerts": [_alert(total_premium="900000", has_sweep=True)],
            "stock-state": {"market_time": "pm", "tape_time": f"{_today()}T08:00:00Z"}}
    F, _ = build(data, spot=100)
    assert F["P8.smart_flow"]["unit"] == "list"       # context-only, never number-tagged (O3)
    assert F["P8.smart_flow"]["history"] == "snapshot"  # independent of live session
