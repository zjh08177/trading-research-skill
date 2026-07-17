import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_meanrev as mod


def _pack():
    return {
        "P9.stretch_sma50_atr": {"v": -1.82, "unit": "ATRs", "asof": "2026-07-17", "src": "x"},
        "P9.stretch_sma200_atr": {"v": 1.42, "unit": "ATRs", "asof": "2026-07-17", "src": "x"},
        "P9.move_atr": {"v": -0.58, "unit": "ATRs", "asof": "2026-07-17", "src": "x"},
        "P9.climax": {"v": False, "unit": "bool", "asof": "2026-07-17", "src": "x"},
        "P9.climax_direction": {"v": None, "unit": "label", "asof": "2026-07-17", "src": "x"},
        "P9.rsi_percentile_conditional": {"v": 52.0, "unit": "pctile", "asof": "2026-07-17", "src": "x"},
        "P9.rsi_percentile_conditional_n": {"v": 14, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.rsi_percentile_note": {"v": "no_edge", "unit": "label", "asof": "2026-07-17", "src": "x"},
        "P9.volume_zscore": {"v": 0.3, "unit": "sigma", "asof": "2026-07-17", "src": "x"},
        "P9.volume_climax_flag": {"v": False, "unit": "bool", "asof": "2026-07-17", "src": "x"},
        "P9.cluster_status": {"v": "clustered", "unit": "label", "asof": "2026-07-17", "src": "x"},
        "P9.cluster_k": {"v": 9, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.cluster_events_n": {"v": 11, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.base_rate_n_raw": {"v": 63, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.base_rate_n_regimes": {"v": 6, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.base_rate_n_macro": {"v": 3, "unit": "count", "asof": "2026-07-17", "src": "x"},
        "P9.base_rate_ci_note": {"v": "no confidence interval computed (n_macro=3 independent "
                                       "cycles is too few for one); treat the table as directional "
                                       "corroboration, not a calibrated probability",
                                 "unit": "label", "asof": "2026-07-17", "src": "x"},
        "P9.base_rate_table": {"v": [{"horizon_days": 20, "n": 56, "mean_pct": 12.6,
                                      "median_pct": 7.7, "winrate_pct": 59.0,
                                      "avg_further_dd_pct": -18.6, "worst_dd_pct": -71.2}],
                              "unit": "table", "asof": "2026-07-17", "src": "x"},
    }


def test_renders_scalar_facts_with_tags(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack()))
    code = mod.main([str(p)])
    out = capsys.readouterr().out
    assert code == 0
    assert "[P9.stretch_sma50_atr]" in out
    assert "[P9.cluster_status]" in out
    assert "meanrev-block" in out  # comment marker present, mirrors riskbox/options


def test_renders_base_rate_table(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack()))
    mod.main([str(p)])
    out = capsys.readouterr().out
    assert "20" in out and "59.0" in out


def test_renders_base_rate_ci_note(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack()))
    mod.main([str(p)])
    out = capsys.readouterr().out
    assert "[P9.base_rate_ci_note]" in out
    assert "not a calibrated probability" in out


def test_renders_rsi_percentile_conditional_n(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack()))
    mod.main([str(p)])
    out = capsys.readouterr().out
    assert "[P9.rsi_percentile_conditional_n]" in out


def test_renders_cluster_events_n(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack()))
    mod.main([str(p)])
    out = capsys.readouterr().out
    assert "[P9.cluster_events_n]" in out


def test_exit_3_when_no_p9_facts(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps({"P1.price": {"v": 1, "unit": "USD", "asof": "x", "src": "x"}}))
    code = mod.main([str(p)])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_empty_base_rate_table_skipped(tmp_path, capsys):
    pack = _pack()
    pack["P9.base_rate_table"]["v"] = []
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(pack))
    code = mod.main([str(p)])
    out = capsys.readouterr().out
    assert code == 0
    # empty list must not print a header-only table
    assert "Forward-return base rate by horizon" not in out
    assert "[P9.base_rate_table]" not in out
