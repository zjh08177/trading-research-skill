import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import move_cluster as mod


def _pack(chg=-13.94):
    return {"P1.chg_pct_1d": {"v": chg, "unit": "pct", "asof": "2026-07-17", "src": "x"}}


def _history_with_crashes(n, crash_days, crash_pct=-14.0):
    """crash_days: set of bar indices (0-based) that crash by crash_pct;
    all other days drift +0.1%."""
    bars = []
    price = 100.0
    for i in range(n):
        if i in crash_days:
            price *= (1 + crash_pct / 100)
        else:
            price *= 1.001
        bars.append({"date": f"2024-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
                     "close": price, "adjClose": price, "volume": 1000})
    return {"ticker": "X", "asof": bars[-1]["date"], "bars": bars}


def _run(tmp_path, pack, history):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps(pack))
    h.write_text(json.dumps(history))
    return mod.main([str(p), str(h)])


def test_isolated_single_crash(tmp_path, capsys):
    hist = _history_with_crashes(200, {199})  # only today crashes
    code = _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.cluster_status"]["v"] == "isolated"
    assert out["P9.cluster_k"]["v"] == 1


def test_clustered_when_recent_crashes_nearby(tmp_path, capsys):
    hist = _history_with_crashes(200, {170, 180, 190, 199})
    code = _run(tmp_path, _pack(), hist)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.cluster_status"]["v"] == "clustered"
    assert out["P9.cluster_k"]["v"] >= 2


def test_today_not_naturally_included_uses_fallback(tmp_path, capsys):
    """The pack's P1.chg_pct_1d (-13.94%) is deliberately a bit more extreme
    than what the history series' own last-day arithmetic produces (-13.5%,
    via crash_pct=-13.5) -- a realistic pack-vs-history mismatch (live/settled
    quote vs. bar-to-bar close). abs(-13.5) < threshold (13.94), and no other
    day in the drifting history is a "down" move at all, so
    comparable_event_indices returns no matches whatsoever -- event_dates
    would be [] without the "today is always its own event" fallback.
    cluster_events([]) returns [], and `next(c for c in clusters if
    dates[-1] in c)` would then raise StopIteration: deleting the fallback
    line doesn't just misclassify today, it crashes the script outright.
    With the fallback, today is force-included as its own singleton event."""
    hist = _history_with_crashes(200, {199}, crash_pct=-13.5)
    code = _run(tmp_path, _pack(chg=-13.94), hist)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P9.cluster_status"]["v"] == "isolated"
    assert out["P9.cluster_k"]["v"] == 1
    assert out["P9.cluster_events_n"]["v"] == 1


def test_missing_required_fact_exit_3(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    h = tmp_path / "11-history.json"
    p.write_text(json.dumps({}))
    h.write_text(json.dumps(_history_with_crashes(100, {99})))
    code = mod.main([str(p), str(h)])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
