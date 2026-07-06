"""Cursor-host judge routing (ensemble side): vote-header parse → judge_mix +
Panel disclosure, kept inside the QA-exempt rating block. Stdlib + pytest."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import ensemble  # noqa: E402


def _vote(tmp, n, rating, conv, model=None, slot=None, backend=None, body="analysis line"):
    """Write vote-<n>.md, optionally prefixed with BACKEND/MODEL/SLOT headers."""
    head = []
    if backend:
        head.append(f"BACKEND: {backend}")
    if model:
        head.append(f"MODEL: {model}")
    if slot is not None:
        head.append(f"SLOT: {slot}")
    prefix = ("\n".join(head) + "\n\n") if head else ""
    (tmp / f"vote-{n}.md").write_text(
        f"{prefix}{body}\nVERDICT: {rating} | CONVICTION: {conv} | WHY: reason {n}.\n")


def _run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True)


# ---------- header parse ----------

def test_header_parse_carries_model():
    model, body = ensemble.parse_headers(
        ["BACKEND: cursor", "MODEL: gpt-5.5-extra-high", "SLOT: 1",
         "analysis line", "VERDICT: Buy | CONVICTION: 7 | WHY: x."])
    assert model == "gpt-5.5-extra-high"
    assert body[0] == "analysis line"          # leading headers consumed


def test_headerless_defaults_to_opus(tmp_path):
    _vote(tmp_path, 1, "Buy", 7)               # no headers = legacy Claude Code
    v = ensemble.parse_vote(tmp_path / "vote-1.md")
    assert v is not None and v[4] == "claude/opus"


def test_slot_is_informational_not_required(tmp_path):
    _vote(tmp_path, 1, "Hold", 5, model="composer-2.5", slot=2)
    v = ensemble.parse_vote(tmp_path / "vote-1.md")
    assert v[0] == ensemble.NOTCH["Hold"] and v[4] == "composer-2.5"


def test_header_only_file_is_malformed(tmp_path):
    """A file with headers but no VERDICT body is malformed (unchanged rule)."""
    (tmp_path / "vote-1.md").write_text(
        "BACKEND: cursor\nMODEL: glm-5.2-high\nSLOT: 3\n")
    assert ensemble.parse_vote(tmp_path / "vote-1.md") is None


def test_headers_are_leading_only():
    lines = ["MODEL: real-slug", "first body line",
             "SLOT: 9 looks-like-header-but-is-body",
             "VERDICT: Sell | CONVICTION: 6 | WHY: x."]
    model, body = ensemble.parse_headers(lines)
    assert model == "real-slug"
    assert body == lines[1:]                    # stopped at the first body line


# ---------- judge_mix + Panel ----------

def test_mixed_panel_judge_mix_and_panel_inside_exempt_region(tmp_path):
    _vote(tmp_path, 1, "Buy", 7, backend="cursor", model="gpt-5.5-extra-high", slot=1)
    _vote(tmp_path, 2, "Hold", 5, backend="cursor",
          model="claude-opus-4-8-thinking-max", slot=2)
    _vote(tmp_path, 3, "Buy", 6, backend="cursor", model="glm-5.2-high", slot=3)
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec["judge_mix"] == ["gpt-5.5-extra-high",
                                "claude-opus-4-8-thinking-max", "glm-5.2-high"]
    assert ("_Panel: gpt-5.5-extra-high + claude-opus-4-8-thinking-max + "
            "glm-5.2-high_") in block
    lines = block.splitlines()
    panel_i = next(i for i, l in enumerate(lines) if l.startswith("_Panel:"))
    actual_i = next(i for i, l in enumerate(lines) if l.startswith("_Actual N:"))
    assert panel_i < actual_i                   # Panel before the exemption terminator


def test_legacy_panel_absent_and_default_mix(tmp_path):
    """Headerless votes → all claude/opus → no Panel line (Claude Code unchanged)."""
    _vote(tmp_path, 1, "Buy", 7)
    _vote(tmp_path, 2, "Buy", 6)
    _vote(tmp_path, 3, "Hold", 5)
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec["judge_mix"] == ["claude/opus", "claude/opus", "claude/opus"]
    assert "_Panel:" not in block


def test_substitution_disclosure_surfaces_in_panel(tmp_path):
    """A substituted slot votes under `auto`; the disclosure rides the MODEL header
    verbatim into judge_mix + the Panel line (a substituted slot shows what voted)."""
    _vote(tmp_path, 1, "Buy", 7, backend="cursor",
          model="auto (SUBSTITUTED gpt-5.5-extra-high)", slot=1)
    _vote(tmp_path, 2, "Hold", 5, backend="cursor",
          model="claude-opus-4-8-thinking-max", slot=2)
    _vote(tmp_path, 3, "Buy", 6, backend="cursor", model="glm-5.2-high", slot=3)
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert "auto (SUBSTITUTED gpt-5.5-extra-high)" in dec["judge_mix"]
    assert "SUBSTITUTED gpt-5.5-extra-high" in block


def test_cli_tally_emits_judge_mix_and_panel(tmp_path):
    _vote(tmp_path, 1, "Buy", 7, backend="cursor", model="gpt-5.5-extra-high", slot=1)
    _vote(tmp_path, 2, "Buy", 6, backend="cursor",
          model="claude-opus-4-8-thinking-max", slot=2)
    _vote(tmp_path, 3, "Hold", 5, backend="cursor", model="glm-5.2-high", slot=3)
    r = _run("ensemble.py", "tally", str(tmp_path), "--n-target", "3")
    assert r.returncode == 0
    dec = json.loads(r.stderr.strip())
    assert dec["judge_mix"] == ["gpt-5.5-extra-high",
                                "claude-opus-4-8-thinking-max", "glm-5.2-high"]
    assert "_Panel:" in r.stdout


# ---------- goldens ----------

def test_golden_n3_publish_qa_green_no_slug_leak(tmp_path):
    """A headered n3 panel renders a publish block whose Panel slug digits stay
    inside the QA exemption — qa_check is green and never flags them untagged."""
    for i, (r, c, m) in enumerate([
            ("Buy", 7, "gpt-5.5-extra-high"),
            ("Hold", 5, "claude-opus-4-8-thinking-max"),
            ("Buy", 6, "glm-5.2-high")], 1):
        _vote(tmp_path, i, r, c, backend="cursor", model=m, slot=i)
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec["decision"] == "publish"
    report = (FIXTURES / "report.md").read_text().replace(
        "<!-- RATING-BLOCK-SLOT -->", block.rstrip())
    assert "_Panel:" in report
    rp = tmp_path / "final.md"
    rp.write_text(report)
    qa = _run("qa_check.py", str(rp), str(FIXTURES / "datapack.json"))
    assert qa.returncode == 0, qa.stdout
    for digit in ("5.5", "4.8", "5.2"):         # slug digits never leak to scan_untagged
        assert f"untagged number '{digit}" not in qa.stdout


def test_golden_escalate_then_publish_n5(tmp_path):
    for i, (r, c, m) in enumerate([
            ("Sell", 6, "gpt-5.5-extra-high"),
            ("Buy", 6, "claude-opus-4-8-thinking-max"),
            ("Hold", 5, "glm-5.2-high")], 1):
        _vote(tmp_path, i, r, c, backend="cursor", model=m, slot=i)
    _, dec3 = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec3["decision"] == "escalate"        # spread 2 at N=3
    _vote(tmp_path, 4, "Hold", 5, backend="cursor", model="composer-2.5", slot=4)
    _vote(tmp_path, 5, "Buy", 6, backend="cursor", model="grok-4.3", slot=5)
    block5, dec5 = ensemble.render(*ensemble.collect(tmp_path), 5)
    assert dec5["decision"] == "publish"         # spread 2 at N=5
    assert len(dec5["judge_mix"]) == 5
    assert "composer-2.5" in block5 and "grok-4.3" in block5
