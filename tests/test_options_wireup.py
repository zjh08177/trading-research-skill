"""A12 wire-up: the --options-only spine is LLM-free by construction (EC8), and a
render_options -> qa_check.main pass over a realistic report exits clean (EC3/EC6
end-to-end, the offline proxy for live Stage-7)."""
import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "vendors"))
import render_options as R  # noqa: E402
import qa_check as qa  # noqa: E402


def test_standalone_spine_is_llm_free():
    # EC8: the standalone spine is uw_options -> render_options; neither may reach
    # an LLM (ensemble/ledger/Agent). This is a structural guard, not a live test.
    for name in ("render_options.py", "scripts/vendors/uw_options.py"):
        src = (ROOT / name if "/" in name else ROOT / "scripts" / name).read_text()
        for banned in ("import ensemble", "import ledger", "Agent(", "anthropic"):
            assert banned not in src, f"{name} references {banned}"


def _fact(v, unit, history, **e):
    return {"v": v, "unit": unit, "asof": "2026-07-06", "src": "uw",
            "history": history, "derived": False, **e}


def _pack():
    return {
        "P1.price": _fact(420.0, "USD", "daily"),
        "P2.atr14": _fact(12.5, "USD", "daily"),
        "P8.gex_regime": _fact("long-gamma", "label", "snapshot", derived=True),
        "P8.gex_net": _fact(812000000.0, "usd", "daily", derived=True),
        "P8.flip_level": _fact(410.0, "price", "snapshot", derived=True),
        "P8.dist_flip": _fact(0.0238, "ratio", "snapshot", derived=True),
        "P8.iv_rank_1y": _fact(42.5, "pct", "snapshot"),
        "P8.iv_now": _fact(0.4512, "ratio", "daily"),
        "P8.rr_skew_25d": _fact(-0.035, "ratio", "daily", label="put-skewed"),
        "P8.max_pain_front": _fact(415.0, "price", "snapshot"),
        "P8.net_prem_ticks": _fact(2200000.0, "usd", "live", session_state="mid"),
        "P8.gex_by_strike": _fact([[450.0, 90000000.0], [400.0, -70000000.0]],
                                  "list", "snapshot"),
        "P8._gaps": ["DATA-THIN(flow): pre-open, live flow withheld"],
    }


def test_render_then_qa_main_exits_clean(tmp_path, capsys):
    pack = _pack()
    block = R.build(pack)
    # a minimal report embedding the block under the Dealer-Positioning heading
    report = (
        "# TSLA — options-only\n\n"
        "## Dealer Positioning & Options\n\n" + block + "\n"
    )
    pack_path = tmp_path / "10-datapack.json"
    rpt_path = tmp_path / "60-report.md"
    pack_path.write_text(json.dumps(pack))
    rpt_path.write_text(report)
    rc = qa.main([str(rpt_path), str(pack_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 fail" in out
    # the DATA-THIN gap surfaced in the block
    assert "DATA-THIN(flow)" in block


def test_render_options_main_writes_block(tmp_path, capsys):
    pack_path = tmp_path / "10-datapack.json"
    pack_path.write_text(json.dumps(_pack()))
    rc = R.main([str(pack_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("<!-- options-block: inserted verbatim")
    assert "[P8.gex_net]" in out and "options-block: end" in out
