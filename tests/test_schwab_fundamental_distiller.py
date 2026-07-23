"""tests for scripts/distillers/schwab_fundamental.py (Step s5-t1 of the
distilled-signal-pack v1 slice): T1 Schwab-fundamentals distillation (R7,
tech-solution §4.2). `raw_rows` here is the raw `_schwab_fundamental` dict
emitted by scripts/vendors/schwab_fundamental.py (a documented-subset
passthrough of the Schwab `fundamental` block) -- the distiller maps it to
cited P3 signals, omitting (never fabricating) absent keys."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from distillers import DistillCtx  # noqa: E402
from distillers import schwab_fundamental as D  # noqa: E402


def _ctx(max_rows=12, asof="2026-07-10"):
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


FULL_FIXTURE = {
    "beta": 1.8,
    "shortIntToFloat": 22.5,
    "shortIntDayToCover": 6.1,
    "peRatio": 30.2,
    "pegRatio": 1.9,
    "pbRatio": 12.3,
    "divYield": 0.6,
    "divAmount": 0.24,
    "bookValuePerShare": 8.15,
    "marketCap": 55000000000,
}


def test_full_fixture_maps_all_and_cites():
    out = D.distill(FULL_FIXTURE, _ctx())
    expected = {
        "P3.beta": ("beta", "index"),
        "P3.short_int_to_float": ("shortIntToFloat", "pct"),
        "P3.short_int_days_to_cover": ("shortIntDayToCover", "days"),
        "P3.pe_vendor": ("peRatio", "ratio"),
        "P3.peg_ratio": ("pegRatio", "ratio"),
        "P3.pb_ratio": ("pbRatio", "ratio"),
        "P3.div_yield": ("divYield", "pct"),
        "P3.div_amount": ("divAmount", "USD"),
        "P3.book_value_ps": ("bookValuePerShare", "USD"),
        "P3.market_cap_vendor": ("marketCap", "USD"),
    }
    ids = {s["id"] for s in out}
    for fid, (key, unit) in expected.items():
        s = _by_id(out, fid)
        assert s is not None, f"missing {fid}"
        assert s["v"] == FULL_FIXTURE[key]
        assert s["unit"] == unit
        assert s["src"] == "schwab(fundamental)"
        assert s["asof"] == "2026-07-10"
    assert ids == set(expected)


def test_partial_fixture_omits_absent_keys():
    raw = {"beta": 1.1, "peRatio": 20.0}
    out = D.distill(raw, _ctx())
    ids = {s["id"] for s in out}
    assert ids == {"P3.beta", "P3.pe_vendor"}
    assert _by_id(out, "P3.short_int_to_float") is None
    assert _by_id(out, "P3.div_yield") is None


def test_notable_short_interest_and_beta():
    raw = {"shortIntToFloat": 25.0, "beta": 2.0}
    out = D.distill(raw, _ctx())
    si = _by_id(out, "P3.short_int_to_float")
    beta = _by_id(out, "P3.beta")
    assert si["notable"] is True
    assert beta["notable"] is True


def test_not_notable_below_thresholds():
    raw = {"shortIntToFloat": 5.0, "beta": 1.0}
    out = D.distill(raw, _ctx())
    si = _by_id(out, "P3.short_int_to_float")
    beta = _by_id(out, "P3.beta")
    assert si.get("notable") in (None, False)
    assert beta.get("notable") in (None, False)


def test_empty_fixture_is_quiet():
    out = D.distill({}, _ctx())
    assert len(out) == 1
    assert out[0]["id"] == "P3.fundamental_quiet"
    assert out[0]["notable"] is False


def test_none_raw_rows_is_quiet():
    out = D.distill(None, _ctx())
    assert len(out) == 1
    assert out[0]["id"] == "P3.fundamental_quiet"


def test_cap_to_max_rows():
    # R3: cap keeps <=max_rows VALUE signals and names the omission (not silent).
    ctx = _ctx(max_rows=3)
    out = D.distill(FULL_FIXTURE, ctx)
    value_sigs = [s for s in out if s.get("v") is not None]
    omissions = [s for s in out if s.get("v") is None and s.get("gap")]
    assert len(value_sigs) <= 3
    assert len(omissions) == 1, "over-cap must emit exactly one named-omission signal"
    assert "kept 3 of" in omissions[0]["gap"]
