"""Tests for qa_check.py cite verification — number parsing + unit awareness."""
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
