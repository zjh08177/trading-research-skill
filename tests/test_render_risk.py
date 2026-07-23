"""Tests for the deterministic risk officer (Feature 21 WS-A, render_risk.py).

Contract: emit the verbatim risk_box block, then TEMPLATED narration derived
only by rule (1R sizing, event risk vs a pinned horizon, concentration). Fail
loud on a missing required box fact; never fabricate; byte-stable output."""
import json
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_risk as mod  # noqa: E402

PY = sys.executable

RISK_PACK = {
    "P1.last": {"v": 244.42, "unit": "USD", "src": "schwab"},
    "P1.price": {"v": 241.91, "unit": "USD", "src": "schwab"},
    "P1.chg_pct_1d": {"v": -6.63, "unit": "pct", "src": "schwab"},
    "P2.atr14": {"v": 27.46, "unit": "USD", "src": "schwab"},
    "P2.atr14_pct": {"v": 11.35, "unit": "pct", "src": "schwab"},
    "P2.sigma30": {"v": 6.69, "unit": "pct", "src": "schwab"},
    "P2.sma50": {"v": 217.35, "unit": "USD", "src": "schwab"},
}


def _write(tmp, name, obj):
    p = tmp / name
    p.write_text(json.dumps(obj))
    return p


def _run(*args):
    return subprocess.run([PY, str(SCRIPTS / "render_risk.py"), *args],
                          capture_output=True, text=True)


# ---- structure: box verbatim + narration ------------------------------------

def test_leads_with_verbatim_box(tmp_path):
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("<!-- riskbox-block: inserted verbatim, do not edit -->")
    assert "riskbox-block: end" in r.stdout
    # box numbers present (byte-equal to risk_box output for the box region)
    import risk_box
    box = risk_box.build(RISK_PACK)
    assert box.rstrip("\n") in r.stdout


def test_output_is_byte_stable(tmp_path):
    """Determinism: two renders of the same pack are byte-identical."""
    p = _write(tmp_path, "10-datapack.json", RISK_PACK)
    a = _run(str(p)).stdout
    b = _run(str(p)).stdout
    assert a == b and a != ""


def test_key_points_present(tmp_path):
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    assert "KEY POINTS:" in r.stdout
    assert "Invalidation (long):" in r.stdout


# ---- sizing / 1R implication ------------------------------------------------

def test_sizing_1r_math(tmp_path):
    """price 244.42, SMA50 217.35, ATR14 27.46 -> long stop 189.89;
    1R = 244.42-189.89 = 54.53 (22.31%, 1.99x ATR14)."""
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    assert "189.89" in r.stdout          # long invalidation anchor
    assert "54.53" in r.stdout           # 1R in dollars
    assert "1.99x ATR14" in r.stdout


def test_sizing_breached_when_price_below_anchor(tmp_path):
    pack = {**RISK_PACK, "P1.last": {"v": 180.0, "unit": "USD", "src": "x"}}
    r = _run(str(_write(tmp_path, "10-datapack.json", pack)))
    assert "already at/below the long invalidation anchor" in r.stdout


# ---- event risk rule --------------------------------------------------------

def test_event_within_horizon_flagged(tmp_path):
    pack = {**RISK_PACK, "P5.next_earnings":
            {"v": "2026-08-01", "unit": "date", "asof": "2026-07-25", "src": "x"}}
    r = _run(str(_write(tmp_path, "10-datapack.json", pack)))
    assert "in 7d" in r.stdout and "WITHIN the 14-day stop horizon" in r.stdout


def test_event_beyond_horizon(tmp_path):
    pack = {**RISK_PACK, "P5.next_earnings":
            {"v": "2026-08-30", "unit": "date", "asof": "2026-07-25", "src": "x"}}
    r = _run(str(_write(tmp_path, "10-datapack.json", pack)))
    assert "beyond the 14-day stop horizon" in r.stdout


def test_event_missing_is_datagap(tmp_path):
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    assert "Event risk: DATA GAP: next earnings date" in r.stdout


def test_event_etf_label_not_fabricated(tmp_path):
    pack = {**RISK_PACK, "P5.next_earnings":
            {"v": "N/A (ETF; no corporate earnings date)", "unit": "label",
             "asof": "2026-07-25", "src": "derived"}}
    r = _run(str(_write(tmp_path, "10-datapack.json", pack)))
    assert "no scheduled corporate earnings" in r.stdout
    assert "DATA GAP" not in r.stdout.split("KEY POINTS")[0].split("Event risk")[1]


# ---- concentration: position-BLIND (invariant 12) ---------------------------

def test_concentration_is_position_blind_principle(tmp_path):
    """The judge-bound risk artifact must NOT carry position facts (invariant 12).
    Concentration is a generic principle, never an H1 number."""
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    assert "Concentration:" in r.stdout
    assert "position-blind here" in r.stdout
    assert "H1." not in r.stdout            # no position facts leak
    assert "of book" not in r.stdout        # no per-name weight number


def test_never_reads_position_even_if_offered(tmp_path):
    """A position arg is rejected (single-arg CLI) — the renderer can never be
    handed 15-position.json in the pipeline."""
    pos = _write(tmp_path, "15-position.json",
                 {"H1.held": {"v": True}, "H1.pct_of_book": {"v": 8.3}})
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)), str(pos))
    assert r.returncode == 2                # too many args → bad-args, never reads it


# ---- fail loud --------------------------------------------------------------

def test_missing_required_fact_exits_3(tmp_path):
    pack = {k: v for k, v in RISK_PACK.items() if k != "P2.sma50"}
    r = _run(str(_write(tmp_path, "10-datapack.json", pack)))
    assert r.returncode == 3
    assert "P2.sma50" in r.stderr


def test_bad_args_exit_2(tmp_path):
    r = _run()
    assert r.returncode == 2


def test_no_ungrounded_editorializing(tmp_path):
    """The BTSG defect: the old LLM pulled P3/P6 facts (beta, margins, crowding)
    into the risk box. The template must cite ONLY box/pack risk facts."""
    r = _run(str(_write(tmp_path, "10-datapack.json", RISK_PACK)))
    for forbidden in ("beta", "margin", "crowding", "P3.", "P6."):
        assert forbidden not in r.stdout, forbidden


# ---- module-level build() (import path) -------------------------------------

def test_build_raises_keyerror_on_missing(tmp_path):
    pack = {k: v for k, v in RISK_PACK.items() if k != "P2.atr14"}
    with pytest.raises(KeyError):
        mod.build(pack)
