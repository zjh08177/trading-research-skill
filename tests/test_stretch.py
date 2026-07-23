import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import stretch as mod


def _pack(**overrides):
    base = {
        "P1.price": {"v": 142.48, "unit": "USD", "asof": "2026-07-17", "src": "x"},
        "P2.sma20": {"v": 210.74, "unit": "USD", "asof": "2026-07-17", "src": "x"},
        "P2.sma50": {"v": 205.21, "unit": "USD", "asof": "2026-07-17", "src": "x"},
        "P2.sma200": {"v": 93.42, "unit": "USD", "asof": "2026-07-17", "src": "x"},
        "P2.atr14": {"v": 34.47, "unit": "USD", "asof": "2026-07-17", "src": "x"},
        "P2.atr14_pct": {"v": 24.19, "unit": "pct", "asof": "2026-07-17", "src": "x"},
        "P2.sigma30": {"v": 13.83, "unit": "pct", "asof": "2026-07-17", "src": "x"},
        "P1.chg_pct_1d": {"v": -13.94, "unit": "pct", "asof": "2026-07-17", "src": "x"},
    }
    base.update(overrides)
    return base


def _run(tmp_path, pack, extra_args=()):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(pack))
    return mod.main([str(p), *extra_args])


def test_stretch_signs_and_magnitude(tmp_path, capsys):
    # Every float stretch fact is emitted at 2dp (an unrounded
    # "2.424549120275739 ATR14 above SMA50" once shipped in a published summary),
    # so the expectation is the exactly-rounded value, not the raw quotient.
    code = _run(tmp_path, _pack())
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.stretch_sma50_atr"]["v"] == round((142.48 - 205.21) / 34.47, 2)
    assert out["P9.stretch_sma200_atr"]["v"] == round((142.48 - 93.42) / 34.47, 2)
    assert out["P9.move_atr"]["v"] == round(-13.94 / 24.19, 2)


def test_climax_flag_false_when_sub_atr(tmp_path, capsys):
    _run(tmp_path, _pack())
    out = json.loads(capsys.readouterr().out)
    assert out["P9.climax"]["v"] is False
    assert out["P9.climax_direction"]["v"] is None


def test_climax_flag_true_when_move_ge_1_5x_atr(tmp_path, capsys):
    pack = _pack(**{"P1.chg_pct_1d": {"v": -40.0, "unit": "pct",
                                       "asof": "2026-07-17", "src": "x"}})
    _run(tmp_path, pack)
    out = json.loads(capsys.readouterr().out)
    assert out["P9.climax"]["v"] is True
    assert out["P9.climax_direction"]["v"] == "down"


def test_no_decay_risk_without_leverage_flag(tmp_path, capsys):
    _run(tmp_path, _pack())
    out = json.loads(capsys.readouterr().out)
    assert "P9.decay_risk_daily_pct" not in out


def test_decay_risk_with_leverage_flag(tmp_path, capsys):
    _run(tmp_path, _pack(), extra_args=["--leverage", "3"])
    out = json.loads(capsys.readouterr().out)
    # sigma_underlying = 13.83/3 = 4.61%; drag = (9-3)/2*(0.0461)^2*100 ~= 0.638
    assert out["P9.decay_risk_daily_pct"]["v"] == pytest_approx(0.638, rel=0.02)


def test_missing_required_fact_exit_3(tmp_path, capsys):
    pack = _pack()
    del pack["P2.atr14"]
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(pack))
    code = mod.main([str(p)])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
    assert "P2.atr14" in out.err


def test_zero_atr14_does_not_crash_and_emits_none(tmp_path, capsys):
    # P2.atr14 == 0.0 is a legitimate present-but-zero fact (not "missing").
    # Chosen behavior (mirrors risk_box.py's own atr_pct==0 "cannot normalize"
    # guard): the CLI still exits 0, and only the three ATR-normalized
    # stretch facts that would divide by atr14 come back as None instead of
    # raising ZeroDivisionError. Facts that don't depend on atr14 (e.g.
    # move_atr, which normalizes by atr14_pct instead) are unaffected.
    pack = _pack(**{"P2.atr14": {"v": 0.0, "unit": "USD",
                                  "asof": "2026-07-17", "src": "x"}})
    code = _run(tmp_path, pack)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.stretch_sma20_atr"]["v"] is None
    assert out["P9.stretch_sma50_atr"]["v"] is None
    assert out["P9.stretch_sma200_atr"]["v"] is None
    assert out["P9.move_atr"]["v"] == round(-13.94 / 24.19, 2)


def test_zero_price_is_used_not_silently_swapped_for_last(tmp_path, capsys):
    # P1.price == 0.0 is a legitimate present fact. The old `or` fallback
    # treated 0.0 as falsy and silently substituted P1.last instead — this
    # must not happen; price=0.0 must be used as-is.
    pack = _pack(**{"P1.price": {"v": 0.0, "unit": "USD",
                                  "asof": "2026-07-17", "src": "x"},
                    "P1.last": {"v": 999.0, "unit": "USD",
                                "asof": "2026-07-17", "src": "x"}})
    code = _run(tmp_path, pack)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.stretch_sma50_atr"]["v"] == round((0.0 - 205.21) / 34.47, 2)


def test_zero_sigma30_emits_zero_decay_risk_not_absent(tmp_path, capsys):
    # sigma30 == 0.0 is a legitimate present fact meaning zero volatility.
    # The decay-risk formula (L^2-L)/2 * sigma_underlying^2 * 100 is
    # mathematically 0.0 in that case, not "unknown" — the old truthiness
    # check (`if sigma`) silently dropped the field entirely instead.
    pack = _pack(**{"P2.sigma30": {"v": 0.0, "unit": "pct",
                                    "asof": "2026-07-17", "src": "x"}})
    code = _run(tmp_path, pack, extra_args=["--leverage", "3"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "P9.decay_risk_daily_pct" in out
    assert out["P9.decay_risk_daily_pct"]["v"] == 0.0


def pytest_approx(x, rel=1e-6):
    import pytest
    return pytest.approx(x, rel=rel)
