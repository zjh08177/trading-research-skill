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
    F, _ = build(data, spot=100, earnings=None)
    assert F["P8.implied_move_front"]["event"] == "event-status-unknown"


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
