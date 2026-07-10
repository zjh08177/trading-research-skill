"""Tests for the qa_check.py replay guard (--replay / --asof-cutoff).

Covers: forbidden PIT families (delegated to replay.check_pack_cutoff), the
NEW report-text scan (unknown-URL hard fail, current-data phrase hard fail),
the false-positive guardrail (bare "latest" inside a tag, and the fixed
"Historical replay" banner must NOT be flagged), and a clean pass-through.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qa_check as qa  # noqa: E402

CUTOFF = "2025-06-21"


def _pack(extra=None):
    base = {
        "P2.macd": {"v": -9.88, "unit": "USD", "asof": "2025-06-15", "src": "test"},
    }
    if extra:
        base.update(extra)
    return base


def _write(tmp_path, name, content):
    p = tmp_path / name
    if name.endswith(".json"):
        p.write_text(json.dumps(content))
    else:
        p.write_text(content)
    return p


CLEAN_REPORT = (
    "# T\n\n"
    "**Historical replay**\n\n"
    "## Thesis\n\n"
    "MACD -9.88 [P2.macd] confirms the setup per the latest filing "
    "[P3.latest_10q_filed].\n"
)


def _run(tmp_path, pack, report_text=CLEAN_REPORT):
    report = _write(tmp_path, "60-report.md", report_text)
    datapack = _write(tmp_path, "10-datapack.json", pack)
    return qa.main([str(report), str(datapack), "--replay", "--asof-cutoff", CUTOFF])


def test_forbidden_family_hard_fails(tmp_path):
    pack = _pack({"P4.chain": {"v": [[1, 2]], "asof": "2025-06-15", "src": "test"}})
    # Report doesn't reference the forbidden fact; the guard still hard-fails
    # on its mere presence in the pack.
    assert _run(tmp_path, pack, "# T\n\n**Historical replay**\n\n## Thesis\n\nClean.\n") != 0


def test_report_url_not_in_datapack_hard_fails(tmp_path):
    pack = _pack()
    report = (
        "# T\n\n**Historical replay**\n\n## Thesis\n\n"
        "MACD -9.88 [P2.macd]. See https://notindatapack.example.com/article for context.\n"
    )
    assert _run(tmp_path, pack, report) != 0


def test_report_url_present_in_datapack_passes(tmp_path):
    url = "https://vendor.example.com/headline-1"
    pack = _pack({
        "P5.headlines": {
            "v": [{"title": "x", "url": url, "published_at": "2025-06-10"}],
            "asof": "2025-06-15", "src": "test",
        },
    })
    report = (
        "# T\n\n**Historical replay**\n\n## Thesis\n\n"
        f"MACD -9.88 [P2.macd]. See {url} for context.\n"
    )
    assert _run(tmp_path, pack, report) == 0


def test_todays_phrase_hard_fails(tmp_path):
    pack = _pack()
    report = (
        "# T\n\n**Historical replay**\n\n## Thesis\n\n"
        "MACD -9.88 [P2.macd]. This is today's catalyst for the move.\n"
    )
    assert _run(tmp_path, pack, report) != 0


def test_websearch_phrase_hard_fails(tmp_path):
    pack = _pack()
    report = (
        "# T\n\n**Historical replay**\n\n## Thesis\n\n"
        "MACD -9.88 [P2.macd]. Per WebSearch results, momentum is building.\n"
    )
    assert _run(tmp_path, pack, report) != 0


def test_bare_latest_tag_and_banner_not_falsely_flagged(tmp_path):
    # [P3.latest_10q_filed] must NOT trip the "latest ..." phrase guardrail,
    # and the fixed "Historical replay" banner must not be flagged either.
    pack = _pack({
        "P3.latest_10q_filed": {"v": "2025-05-01", "unit": "date",
                                 "asof": "2025-06-15", "src": "test"},
    })
    assert _run(tmp_path, pack, CLEAN_REPORT) == 0


def test_clean_pit_pack_and_report_passes(tmp_path):
    pack = _pack()
    report = "# T\n\n**Historical replay**\n\n## Thesis\n\nMACD -9.88 [P2.macd].\n"
    assert _run(tmp_path, pack, report) == 0


def test_non_replay_invocation_unaffected_by_replay_only_failures(tmp_path):
    # Without --replay, forbidden-family / phrase / URL checks never run —
    # only the pre-existing cite check applies.
    pack = _pack({"P4.chain": {"v": [[1, 2]], "asof": "2025-06-15", "src": "test"}})
    report = _write(tmp_path, "60-report.md",
                     "# T\n\n## Thesis\n\nMACD -9.88 [P2.macd]. today's catalyst.\n")
    datapack = _write(tmp_path, "10-datapack.json", pack)
    assert qa.main([str(report), str(datapack)]) == 0
