"""Tests for qa_check.py cite verification — number parsing + unit awareness."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qa_check as qa  # noqa: E402


def _pack():
    def f(v, unit="USD"):
        return {"v": v, "unit": unit, "asof": "2026-07-05", "src": "test"}
    return {"P2.macd": f(-9.88), "P2.macd_signal": f(-10.97),
            "H1.unrealized_pl": f(-1750.0), "P4.atm_iv_near": f(1.091, "ratio")}


def _run(md, pack):
    return qa.check_pairs(qa.strip_riskbox(md), pack)


def test_typographic_minus_parses_negative():
    # writer emits U+2212 MINUS (−), not ASCII '-'; main() normalizes it.
    md = "MACD −9.88 [P2.macd] over signal −10.97 [P2.macd_signal], loss −$1,750 [H1.unrealized_pl]."
    md = md.replace("−", "-")  # main() does this before check_pairs
    res = _run(md, _pack())
    assert res and all(ok for ok, _ in res), [m for ok, m in res if not ok]


def test_ascii_minus_still_negative():
    res = _run("MACD -9.88 [P2.macd].", _pack())
    assert res[0][0] is True


def test_ratio_pct_cite_matches():
    # a %-suffixed cite of a unit:ratio fact compares num/100 (1.091 == 109.1%)
    res = _run("IV 109.1% [P4.atm_iv_near].", _pack())
    assert res[0][0] is True


def test_real_sign_flip_still_fails():
    # report claims a POSITIVE macd where pack is negative -> must fail
    res = _run("MACD 9.88 [P2.macd].", _pack())
    assert res[0][0] is False


def test_strict_mode_fails_untagged_numbers(tmp_path):
    report = tmp_path / "report.md"
    pack = tmp_path / "pack.json"
    report.write_text("# T\n\n## Thesis\n\nRevenue grew 12.3% without a tag.\n")
    pack.write_text("{}")
    assert qa.main([str(report), str(pack), "--strict"]) == 1


BAD_LEVELS_REPORT = """# SOXL
### Ensemble Rating: **Hold**

## Risk box
LEVELS_JSON:
```json
{"schema": 2, "spot": 142.48, "triggers": [
  {"side": "downside", "intended_action": "Buy", "level": 140.0,
   "basis": "test", "comparison": "close_below", "action_strength": "act",
   "rating_gate": "none", "conditions": []}
]}
```
"""

GOOD_LEVELS_REPORT = BAD_LEVELS_REPORT.replace('"act"', '"review"')


def test_hard_fail_on_invariant_19_violation(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text(BAD_LEVELS_REPORT)
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    code = qa.main([str(report), str(pack)])
    assert code == 1


def test_valid_review_only_levels_do_not_hard_fail_on_invariant_19(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text(GOOD_LEVELS_REPORT)
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    code = qa.main([str(report), str(pack)])
    assert code == 0


def test_invariant_19_hard_fail_independent_of_strict(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text(BAD_LEVELS_REPORT)
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    code = qa.main([str(report), str(pack), "--strict"])
    assert code == 1


FABRICATED_DEBATE = """## Bull case

The primary bull argument is trend context: price sits above SMA200.

## Bear case

Attack the bull's best argument — "oversold bounce is imminent, buy the dip":
the numbers refute the climax framing.
"""

FAITHFUL_DEBATE = """## Bull case

The primary bull argument is "trend context is intact" and price sits above
SMA200.

## Bear case

Attack the bull's best argument — "trend context is intact": the numbers
refute the climax framing regardless of trend.
"""


def test_debate_fidelity_catches_fabricated_bull_quote():
    results = qa.check_debate_fidelity(FABRICATED_DEBATE)
    assert results and results[0][0] is False
    assert "oversold bounce is imminent" in results[0][1]


def test_debate_fidelity_passes_faithful_quote():
    results = qa.check_debate_fidelity(FAITHFUL_DEBATE)
    assert results and all(ok for ok, _ in results)


def test_debate_fidelity_noop_without_sections():
    assert qa.check_debate_fidelity("no headings here") == []


def test_main_hard_fails_on_fabricated_debate_quote(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    debate = tmp_path / "30-debate.md"
    debate.write_text(FABRICATED_DEBATE)
    code = qa.main([str(report), str(pack), "--debate", str(debate)])
    assert code == 1


def test_main_passes_with_faithful_debate_quote(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    debate = tmp_path / "30-debate.md"
    debate.write_text(FAITHFUL_DEBATE)
    code = qa.main([str(report), str(pack), "--debate", str(debate)])
    assert code == 0


def _gap_pack():
    def f(v, unit="USD"):
        return {"v": v, "unit": unit, "asof": "2026-07-16", "src": "test"}
    return {"P2.atr14": f(34.47)}


def test_data_gap_hallucination_catches_present_fact():
    text = "DATA GAP: ATR14 [P2.atr14] not present in pack, so no ATR sizing."
    results = qa.check_data_gap_hallucination(text, _gap_pack())
    assert results and results[0][0] is False
    assert "P2.atr14" in results[0][1]


def test_data_gap_hallucination_allows_real_gap():
    text = "DATA GAP: P3 (issuer financials) is entirely missing for this ETF."
    results = qa.check_data_gap_hallucination(text, _gap_pack())
    assert results == []


def test_data_gap_hallucination_ignores_distant_unrelated_tag():
    # a real gap claim earlier in a long line must not flag an unrelated,
    # correctly-cited present tag much later in the same paragraph
    text = ("the tone component is a DATA GAP (api down). Per house rule, do "
            "not infer sentiment from crowding alone; the label [P2.atr14] "
            "reflects crowding only.")
    assert qa.check_data_gap_hallucination(text, _gap_pack()) == []


def test_data_gap_hallucination_dedupes_multiple_cues_same_fact():
    text = "DATA GAP: ATR14 [P2.atr14] not present in pack, not available anywhere."
    results = qa.check_data_gap_hallucination(text, _gap_pack())
    assert len(results) == 1


def test_main_hard_fails_on_brief_gap_hallucination(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text(json.dumps(_gap_pack()))
    brief = tmp_path / "20-analyst-fund.md"
    brief.write_text("DATA GAP: ATR14 [P2.atr14] not present in pack.")
    code = qa.main([str(report), str(pack), "--brief", str(brief)])
    assert code == 1


def test_main_hard_fails_on_missing_prose_qa_artifact(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    missing = tmp_path / "70-qa-prose.txt"  # never written
    code = qa.main([str(report), str(pack), "--prose-qa", str(missing)])
    assert code == 1


def test_main_hard_fails_on_empty_prose_qa_artifact(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    prose = tmp_path / "70-qa-prose.txt"
    prose.write_text("   \n")  # whitespace-only counts as empty
    code = qa.main([str(report), str(pack), "--prose-qa", str(prose)])
    assert code == 1


def test_main_passes_with_clean_prose_qa_artifact(tmp_path):
    report = tmp_path / "60-report.md"
    report.write_text("# T\n")
    pack = tmp_path / "10-datapack.json"
    pack.write_text("{}")
    prose = tmp_path / "70-qa-prose.txt"
    prose.write_text("PROSE QA: clean")
    code = qa.main([str(report), str(pack), "--prose-qa", str(prose)])
    assert code == 0
