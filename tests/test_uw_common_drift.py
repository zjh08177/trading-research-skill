"""Drift guard: the vendored _uw_common.py must stay byte-equal to the
quant-engine-skill SSOT (tech-solution §1). A copy is the pragmatic install
choice, but silent divergence of the UW transport across the two skills is a
known multi-repo hazard — freeze it here so drift fails loud."""
import pathlib

SKILL_COPY = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vendors" / "_uw_common.py"
SSOT = pathlib.Path(
    "/Users/bytedance/Work/sidekicks/tradingagents-workspace/"
    "quant-engine-skill/scripts/vendors/_uw_common.py"
)


def test_uw_common_byte_equal_to_ssot():
    assert SKILL_COPY.exists(), f"vendored copy missing: {SKILL_COPY}"
    if not SSOT.exists():
        # SSOT absent on this host (e.g. CI without the sibling skill): skip, don't fail.
        import pytest
        pytest.skip(f"SSOT not present: {SSOT}")
    assert SKILL_COPY.read_bytes() == SSOT.read_bytes(), (
        "vendored _uw_common.py has DRIFTED from the quant-engine-skill SSOT; "
        "re-copy or reconcile before shipping."
    )
