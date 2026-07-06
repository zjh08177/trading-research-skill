"""Self-containment guard for the vendored UW transport.

The quant-engine-skill SSOT _uw_common now couples to that skill's ``engine.cli``
package (absent here), so a byte-copy would break at import. Instead this skill
carries a SELF-CONTAINED _uw_common; this test freezes that property — it must
import with no cross-skill / dotenv coupling and expose the transport API. If
someone re-copies the coupled SSOT, the import assertions fail loud."""
import pathlib
import sys

VENDORS = str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vendors")
if VENDORS not in sys.path:
    sys.path.insert(0, VENDORS)


def test_uw_common_is_self_contained():
    src = (pathlib.Path(VENDORS) / "_uw_common.py").read_text()
    assert "from engine " not in src and "import engine" not in src and "from engine." not in src, (
        "vendored _uw_common re-coupled to quant-engine engine.cli — keep it standalone"
    )
    assert "load_dotenv" not in src and "import dotenv" not in src and "import _common" not in src, (
        "_uw_common must not drag in _common/dotenv — keep the UW seam dependency-light"
    )


def test_uw_common_exposes_transport_api():
    import _uw_common as uw
    for name in ("get_json", "data_or_die", "api_key", "emit", "fact", "write_atomic", "BASE"):
        assert hasattr(uw, name), f"_uw_common missing {name}"
    assert uw.BASE == "https://api.unusualwhales.com"
