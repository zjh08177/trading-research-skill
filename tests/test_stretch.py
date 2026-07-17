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
    code = _run(tmp_path, _pack())
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.stretch_sma50_atr"]["v"] == pytest_approx((142.48 - 205.21) / 34.47)
    assert out["P9.stretch_sma200_atr"]["v"] == pytest_approx((142.48 - 93.42) / 34.47)
    assert out["P9.move_atr"]["v"] == pytest_approx(-13.94 / 24.19)


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


def pytest_approx(x, rel=1e-6):
    import pytest
    return pytest.approx(x, rel=rel)
