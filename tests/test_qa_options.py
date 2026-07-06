"""qa_check OPTIONS_BLOCK exemption (A7). The Dealer-Positioning context tables
are untagged by design; scan_untagged must skip the well-formed block while
check_pairs still verifies every tagged P8 scalar inside it."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import qa_check as qa  # noqa: E402


def _pack():
    def f(v, unit="usd"):
        return {"v": v, "unit": unit, "asof": "2026-07-06", "src": "uw"}
    return {"P8.gex_net": f(812000000.0), "P8.gex_series": f([[1, 2]], "list")}


BLOCK = (
    "## Dealer Positioning & Options\n"
    "<!-- options-block: inserted verbatim, do not edit -->\n"
    "### Dealer positioning & options (computed)\n"
    "- Net GEX: $812,000,000.00 [P8.gex_net] (daily)\n"
    "- Gamma regime: **long-gamma** [P8.gex_regime] (snapshot)\n"
    "\n"
    "**GEX by strike** [P8.gex_series] (snapshot):\n"
    "| Strike | Net GEX |\n"
    "|---|---|\n"
    "| 450.00 | 90,000,000.00 |\n"
    "<!-- options-block: end -->\n"
)


def test_untagged_table_cells_not_warned_inside_block():
    # The "Positioning" heading matches the "position" CHECK_SECTION keyword, so
    # without the exempt these cells would warn. With it, they must not.
    warnings = qa.scan_untagged(BLOCK)
    assert not any("450" in w or "90,000,000" in w for w in warnings), warnings


def test_check_pairs_still_verifies_tagged_scalar_in_block():
    # check_pairs is NOT stripped for the options block (unlike the risk box).
    res = qa.check_pairs(qa.strip_riskbox(BLOCK), _pack())
    assert any(ok and "P8.gex_net" in m for ok, m in res)
    assert not any(not ok for ok, m in res), [m for ok, m in res if not ok]


def test_position_section_still_warns_outside_block():
    # Load-bearing control: scan_untagged DOES fire in a position section, so the
    # exempt (not a dead heading) is what protects the block.
    rpt = "## Your position\n- Weight is 5.2 of book with no tag.\n"
    assert any("5.2" in w for w in qa.scan_untagged(rpt))


def test_number_tagged_to_list_fact_hard_fails():
    # A P8 list fact must never carry an adjacent number.
    res = qa.check_pairs("GEX ladder 90,000,000 [P8.gex_series].", _pack())
    assert any(not ok and "non-scalar" in m for ok, m in res)


def test_unterminated_block_is_still_scanned():
    # Fail-safe: a start marker with no end must not swallow the rest of the report.
    truncated = BLOCK.replace("<!-- options-block: end -->\n", "")
    assert any("450" in w or "90,000,000" in w for w in qa.scan_untagged(truncated))
