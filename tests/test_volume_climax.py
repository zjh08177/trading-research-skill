import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import volume_climax as mod


def _pack():
    return {"P1.price": {"v": 100.0, "unit": "USD", "asof": "2026-07-17", "src": "x"}}


def _history(volumes):
    bars = [{"date": f"2026-01-{1 + i:02d}" if i < 28 else f"2026-02-{1 + i - 28:02d}",
             "close": 100.0, "adjClose": 100.0, "volume": v}
            for i, v in enumerate(volumes)]
    return {"ticker": "X", "asof": bars[-1]["date"], "bars": bars}


def _run(tmp_path, volumes):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(_pack()))
    h.write_text(json.dumps(_history(volumes)))
    return mod.main([str(p), str(h)])


def _make_baseline(n):
    """Create n bars with mildly-varying baseline (alternating 900, 1100).
    Mean ≈ 1000, SD ≈ 100. This is realistic and gives z-scores meaning."""
    return [900 if i % 2 == 0 else 1100 for i in range(n)]


def test_flat_volume_no_climax(tmp_path, capsys):
    # 62 bars of baseline (alternating 900/1100). No day deviates enough for z >= 2.0.
    # At 900: z = (900-1000)/100 = -1.0. At 1100: z = (1100-1000)/100 = 1.0.
    volumes = _make_baseline(62)
    code = _run(tmp_path, volumes)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.volume_climax_flag"]["v"] is False
    assert out["P9.volume_decay_flag"]["v"] is False


def test_volume_spike_flags_climax(tmp_path, capsys):
    # 61 bars of baseline (mean ≈ 1000, SD ≈ 100), then spike at 2000.
    # Pop (indices 1-60): 30 bars of 900, 30 bars of 1100 => mean=1000, SD=100.
    # z = (2000 - 1000) / 100 = 10.0 >> 2.0.
    volumes = _make_baseline(61) + [2000]
    code = _run(tmp_path, volumes)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.volume_climax_flag"]["v"] is True
    assert out["P9.volume_zscore"]["v"] > 2.0


def test_decay_after_recent_climax_flagged(tmp_path, capsys):
    # 60 bars of baseline, spike at index 60 (2000, k=2 days ago), normal at 61, decay at 62 (600).
    # At index 60: pop = volumes[0:60], z = (2000 - 1000) / 100 = 10.0 >> 2.0.
    # At index 62 (today): 600 <= 2000 * 0.60 = 1200? Yes, so decay_flag = True.
    volumes = _make_baseline(60) + [2000, 1000, 600]
    code = _run(tmp_path, volumes)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.volume_decay_flag"]["v"] is True


def test_no_decay_when_no_recent_climax(tmp_path, capsys):
    # 62 bars of baseline (alternating 900/1100). No climax in last 3 sessions.
    volumes = _make_baseline(62)
    code = _run(tmp_path, volumes)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.volume_decay_flag"]["v"] is False


def test_too_short_history_exit_3(tmp_path, capsys):
    code = _run(tmp_path, [1000] * 30)
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
