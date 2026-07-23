# tests/test_percentile.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import percentile as mod


def _pack():
    return {
        "P2.rsi14": {"v": 40.3, "unit": "index", "asof": "2026-07-17", "src": "x"},
        "P1.chg_pct_1d": {"v": -13.94, "unit": "pct", "asof": "2026-07-17", "src": "x"},
        "P2.atr14_pct": {"v": 24.19, "unit": "pct", "asof": "2026-07-17", "src": "x"},
    }


def _history(n=400, seed_crash_every=40):
    """Synthetic series with a periodic ~-14% crash day every `seed_crash_every`
    sessions, otherwise small random-walk-free drift, so RSI at each crash is
    reproducibly near the same value (deterministic fixture, no randomness)."""
    bars = []
    price = 100.0
    for i in range(n):
        if i > 0 and i % seed_crash_every == 0:
            price *= 0.86  # -14%
        else:
            price *= 1.01
        bars.append({"date": f"2024-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
                     "close": price, "adjClose": price, "volume": 1000})
    return {"ticker": "X", "asof": bars[-1]["date"], "bars": bars}


def _run(tmp_path, pack, history):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(pack))
    h.write_text(json.dumps(history))
    return mod.main([str(p), str(h)])


def test_percentile_all_and_conditional_present(tmp_path, capsys):
    code = _run(tmp_path, _pack(), _history())
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert 0 <= out["P9.rsi_percentile_all"]["v"] <= 100
    assert out["P9.rsi_percentile_conditional_n"]["v"] >= 5
    assert out["P9.rsi_percentile_note"]["v"] in ("no_edge", "differentiating")


def test_insufficient_conditional_sample_note(tmp_path, capsys):
    # A short history with only 1-2 comparable crash days never reaches the
    # n>=5 floor -> note must be "insufficient_sample", value null.
    hist = _history(n=60, seed_crash_every=1000)  # no crash ever fires
    code = _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.rsi_percentile_conditional"]["v"] is None
    assert out["P9.rsi_percentile_note"]["v"] == "insufficient_sample"


def test_missing_required_fact_exit_3(tmp_path, capsys):
    pack = _pack()
    del pack["P2.rsi14"]
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(pack))
    h.write_text(json.dumps(_history()))
    code = mod.main([str(p), str(h)])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_too_short_history_exit_3(tmp_path, capsys):
    code = _run(tmp_path, _pack(), _history(n=20))
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_zero_chg_pct_legitimate_zero_not_crash(tmp_path, capsys):
    """Regression test: P1.chg_pct_1d == 0.0 (legitimate zero, not missing)
    should not cause division error or crash. Threshold becomes 0.0, matching
    any move magnitude >= 0, which is the correct behavior."""
    pack = _pack()
    pack["P1.chg_pct_1d"]["v"] = 0.0  # legitimate zero
    code = _run(tmp_path, pack, _history())
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert 0 <= out["P9.rsi_percentile_all"]["v"] <= 100
    # With threshold=0, we capture all events with any magnitude >= 0,
    # so conditional_n should be high
    assert out["P9.rsi_percentile_conditional_n"]["v"] >= 5
