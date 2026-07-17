import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import move_base_rate as mod


def _pack(chg=-13.94):
    return {"P1.chg_pct_1d": {"v": chg, "unit": "pct", "asof": "2026-07-17", "src": "x"}}


def _history_with_crashes(n, crash_days, crash_pct=-14.0):
    bars = []
    price = 100.0
    for i in range(n):
        if i in crash_days:
            price *= (1 + crash_pct / 100)
        else:
            price *= 1.01
        bars.append({"date": f"2020-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}"
                     if i < 336 else f"2024-{1 + ((i - 336) // 28) % 12:02d}-{1 + (i - 336) % 28:02d}",
                     "close": price, "adjClose": price, "volume": 1000})
    return {"ticker": "X", "asof": bars[-1]["date"], "bars": bars}


def _run(tmp_path, pack, history):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(pack))
    h.write_text(json.dumps(history))
    return mod.main([str(p), str(h)])


def test_base_rate_table_and_ns_present(tmp_path, capsys):
    crash_days = {60, 61, 62, 250, 251, 400}
    hist = _history_with_crashes(450, crash_days)
    code = _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.base_rate_n_raw"]["v"] >= 1
    assert out["P9.base_rate_n_regimes"]["v"] >= 1
    assert out["P9.base_rate_n_macro"]["v"] >= 1
    assert out["P9.base_rate_direction"]["v"] == "down"
    table = out["P9.base_rate_table"]["v"]
    horizons = {row["horizon_days"] for row in table}
    assert horizons == {5, 10, 20, 40, 60}
    for row in table:
        assert "mean_pct" in row and "winrate_pct" in row and "worst_dd_pct" in row


def test_n_regimes_less_than_or_equal_n_raw(tmp_path, capsys):
    crash_days = {60, 61, 62, 63, 250, 251, 400, 401, 402}
    hist = _history_with_crashes(450, crash_days)
    _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert out["P9.base_rate_n_regimes"]["v"] <= out["P9.base_rate_n_raw"]["v"]
    assert out["P9.base_rate_n_macro"]["v"] <= out["P9.base_rate_n_regimes"]["v"]


def test_ci_note_always_present_and_labeled(tmp_path, capsys):
    hist = _history_with_crashes(450, {60, 250, 400})
    _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert "n_macro=" in out["P9.base_rate_ci_note"]["v"]


def test_missing_required_fact_exit_3(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps({}))
    h.write_text(json.dumps(_history_with_crashes(450, {60, 250, 400})))
    code = mod.main([str(p), str(h)])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_too_short_history_exit_3(tmp_path, capsys):
    hist = _history_with_crashes(100, {99})
    code = _run(tmp_path, _pack(), hist)
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
