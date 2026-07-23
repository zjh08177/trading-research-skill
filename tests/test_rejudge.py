"""Tests for the Feature 21 WS-A ablation harness (rejudge.py).

Covers the deterministic machinery — bundle surgery, faithful judge-prompt
assembly, faithfulness classification, and an end-to-end ablate with a MOCK
worker (no real judges) proving flip detection + tally plumbing."""
import json
import os
import pathlib
import stat
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import rejudge as R  # noqa: E402

RISK_PACK = {
    "P1.last": {"v": 244.42, "unit": "USD", "src": "x"},
    "P1.chg_pct_1d": {"v": -6.63, "unit": "pct", "src": "x"},
    "P2.atr14": {"v": 27.46, "unit": "USD", "src": "x"},
    "P2.atr14_pct": {"v": 11.35, "unit": "pct", "src": "x"},
    "P2.sigma30": {"v": 6.69, "unit": "pct", "src": "x"},
    "P2.sma50": {"v": 217.35, "unit": "USD", "src": "x"},
}
BUNDLE = ("# T judge bundle\n\n## DATA PACK\n\nfacts\n\n## Fundamental analyst\n\n"
          "brief\n\n## CANONICAL DEBATE\n\ndebate\n\n## RISK OFFICER\n\nOLD RISK\n")


# ---- bundle surgery ---------------------------------------------------------

def test_swap_risk_section_replaces_only_risk():
    out = R.swap_risk_section(BUNDLE, "NEW RISK BODY")
    assert "## DATA PACK" in out and "## CANONICAL DEBATE" in out  # preserved
    assert "OLD RISK" not in out
    assert out.endswith("## RISK OFFICER\n\nNEW RISK BODY\n")


def test_swap_risk_section_uses_last_header():
    b = BUNDLE.replace("debate", "mentions ## RISK OFFICER inline")
    out = R.swap_risk_section(b, "NEW")
    # only the FINAL section header is the swap point; earlier text preserved
    assert out.count("## RISK OFFICER") >= 1
    assert out.endswith("NEW\n")


def test_swap_risk_section_raises_without_header():
    with pytest.raises(ValueError):
        R.swap_risk_section("# bundle with no risk section\n", "X")


# ---- judge prompt faithfulness ----------------------------------------------

def test_build_judge_prompt_structure():
    p = R.build_judge_prompt(BUNDLE, "T-run")
    assert p.startswith("ROLE: judge\nRUN: T-run")
    assert "HEADLESS" in p
    assert BUNDLE.strip() in p                       # bundle fills the datapack slot
    assert "(the bundle reproduced above)" in p      # judge_bundle slot filled
    assert "VERDICT: <StrongSell|Sell|Hold|Buy|StrongBuy>" in p
    assert "OUTPUT CONTRACT" in p


def test_build_judge_prompt_differs_only_by_bundle():
    a = R.build_judge_prompt(BUNDLE, "T")
    b = R.build_judge_prompt(R.swap_risk_section(BUNDLE, "DIFFERENT RISK"), "T")
    # the two prompts differ ONLY in the risk region
    assert a != b
    assert a.replace("OLD RISK", "X") == b.replace("DIFFERENT RISK", "X")


# ---- Gate 2 faithfulness classification -------------------------------------

def test_faithfulness_flags_out_of_box_tags(tmp_path, capsys):
    rd = tmp_path / "TCK-2026-01-01-0000"
    rd.mkdir()
    (rd / "40-risk.md").write_text(
        "### Risk box\n- ATR14 [P2.atr14]; SMA50 [P2.sma50]\n"
        "narration: beta [P3.beta], crowding [P6.social_risk], earnings [P5.next_earnings]\n")
    R.faithfulness([rd], None)
    out = json.loads(capsys.readouterr().out)
    assert out["n_runs"] == 1
    dropped = out["runs"][0]["dropped_tags"]
    assert "P3.beta" in dropped and "P6.social_risk" in dropped
    assert "P2.atr14" not in dropped        # box fact — derivable
    assert "P5.next_earnings" not in dropped  # template-extra — reproduced


# ---- end-to-end ablate with a mock worker -----------------------------------

def _mock_worker(tmp_path):
    """A worker that votes Sell when the render_risk signature is present
    (template arm) and Hold otherwise (full arm) — forces a deterministic flip."""
    w = tmp_path / "mock.sh"
    w.write_text(
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do shift; done\n"
        "P=$(cat)\n"
        'echo "[mock] receipt: /tmp/m.json" >&2\n'
        'if echo "$P" | grep -q "templated deterministically from the box"; then\n'
        '  echo "VERDICT: Sell | CONVICTION: 6 | ENTRY-PATH: n/a | WHY: t"\n'
        "else\n"
        '  echo "VERDICT: Hold | CONVICTION: 5 | ENTRY-PATH: n/a | WHY: f"\n'
        "fi\n")
    w.chmod(w.stat().st_mode | stat.S_IEXEC)
    return w


def _make_run(tmp_path, name):
    """A run with the component artifacts the harness reconstructs the bundle from."""
    rd = tmp_path / name
    rd.mkdir()
    (rd / "10-datapack.json").write_text(json.dumps(RISK_PACK))
    (rd / "10-datapack.md").write_text("# pack\nfacts")
    (rd / "30-debate.md").write_text("## Bull case\nb\n## Bear case\nb")
    (rd / "40-risk.md").write_text(
        "### Risk box (computed)\n- ATR14 [P2.atr14]\nOLD LLM NARRATION on beta [P3.beta].\n")
    for role in ("fund", "tech", "sent", "meanrev"):
        (rd / f"20-analyst-{role}.md").write_text(f"{role} brief")
    return rd


def test_reconstruct_bundle_faithful(tmp_path):
    rd = _make_run(tmp_path, "TCK-2026-01-03-0000")
    b = R.reconstruct_bundle(rd)
    assert b.startswith("# TCK judge bundle — immutable Stage 5 input")
    for title in R.ANALYST_TITLE.values():
        assert f"## {title}" in b
    assert b.rstrip().endswith("OLD LLM NARRATION on beta [P3.beta].")
    assert "## RISK OFFICER" in b and "## CANONICAL DEBATE" in b


def test_reconstruct_returns_none_without_risk(tmp_path):
    rd = tmp_path / "X-2026-01-01-0000"
    rd.mkdir()
    (rd / "10-datapack.md").write_text("p")
    (rd / "30-debate.md").write_text("d")
    assert R.reconstruct_bundle(rd) is None  # no 40-risk.md


def test_ablate_end_to_end_detects_flip(tmp_path):
    run = _make_run(tmp_path, "TCK-2026-01-01-0000")
    worker = _mock_worker(tmp_path)
    out = tmp_path / "res.json"
    rc = R.ablate([run], ["m1", "m2", "m3"], str(worker), jobs=4,
                  timeout_s=60, out_path=str(out))
    assert rc == 0
    d = json.loads(out.read_text())
    assert d["n_runs_counted"] == 1
    assert d["flips"] == 1 and d["flip_rate"] == 1.0
    row = d["runs"]["TCK-2026-01-01-0000"]
    assert row["full"]["mode_label"] == "Hold"
    assert row["template"]["mode_label"] == "Sell"
    assert row["flipped"] is True


def test_ablate_no_flip_when_worker_agnostic(tmp_path):
    run = _make_run(tmp_path, "TCK-2026-01-02-0000")
    w = tmp_path / "mockflat.sh"
    w.write_text("#!/usr/bin/env bash\nwhile [[ $# -gt 0 ]]; do shift; done\ncat >/dev/null\n"
                 'echo "VERDICT: Hold | CONVICTION: 5 | ENTRY-PATH: n/a | WHY: flat"\n')
    w.chmod(w.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "res.json"
    R.ablate([run], ["m1", "m2", "m3"], str(w), jobs=4, timeout_s=60, out_path=str(out))
    d = json.loads(out.read_text())
    assert d["flips"] == 0 and d["flip_rate"] == 0.0
    assert d["GATE1_PASS"] is True   # 0 flips, 0 dispersion change
