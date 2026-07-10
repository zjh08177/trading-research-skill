"""Unit tests for the historical as-of replay contract helpers (scripts/replay.py).

Covers ERD acceptance criteria AC1 (date grammar + scope), AC2 (forbidden PIT
families), AC4 (headline timestamp filter), plus the findings.md guardrails:
- P3 sec-edgar facts hard-fail without known_at in replay
- date-valued future facts are allowed only when covered by known_at <= cutoff
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import replay  # noqa: E402

TODAY = dt.date(2026, 7, 10)
CUT = "2025-06-21"


# ---- AC1: date grammar + mode -------------------------------------------------

def test_parse_iso_and_slash_normalize():
    assert replay.parse_cutoff_token("2025-06-21", today=TODAY) == dt.date(2025, 6, 21)
    assert replay.parse_cutoff_token("2025/06/21", today=TODAY) == dt.date(2025, 6, 21)


@pytest.mark.parametrize("bad", ["2025-6-21", "06/21/2025", "2025.06.21", "20250621", "", "yesterday"])
def test_parse_rejects_malformed(bad):
    with pytest.raises(replay.CutoffError):
        replay.parse_cutoff_token(bad, today=TODAY)


def test_parse_rejects_future():
    with pytest.raises(replay.CutoffError):
        replay.parse_cutoff_token("2099-01-01", today=TODAY)


def test_parse_rejects_invalid_calendar_date():
    with pytest.raises(replay.CutoffError):
        replay.parse_cutoff_token("2025-02-30", today=TODAY)


def test_mode_today_is_live_past_is_replay():
    assert replay.mode_for_cutoff(TODAY, today=TODAY) == "live"
    assert replay.mode_for_cutoff(dt.date(2025, 6, 21), today=TODAY) == "replay"


# ---- AC1: scope artifacts -----------------------------------------------------

def test_write_scope_replay_has_banner_and_both_dates(tmp_path):
    jp, mp = replay.write_scope(str(tmp_path), {
        "mode": "replay", "ticker": "TSLA", "asset_class": "equity",
        "requested_cutoff": CUT, "effective_market_asof": "2025-06-20",
        "entry_market_asof": "2025-06-23", "generated_at": "2026-07-10T13:00:00Z",
    })
    scope = json.loads(Path(jp).read_text())
    assert scope["mode"] == "replay"
    assert scope["requested_cutoff"] == CUT
    assert scope["effective_market_asof"] == "2025-06-20"
    assert scope["entry_market_asof"] == "2025-06-23"
    md = Path(mp).read_text()
    assert "Historical replay" in md
    assert CUT in md and "2025-06-20" in md and "2025-06-23" in md


def test_write_scope_conservative_fallback_note(tmp_path):
    _, mp = replay.write_scope(str(tmp_path), {
        "mode": "replay", "ticker": "TSLA", "requested_cutoff": CUT,
        "effective_market_asof": "2025-06-20", "entry_market_asof": "2025-06-20",
        "conservative_fallback": True, "generated_at": "2026-07-10T13:00:00Z",
    })
    assert "Conservative fallback" in Path(mp).read_text()


def test_write_scope_live_has_no_replay_banner(tmp_path):
    _, mp = replay.write_scope(str(tmp_path), {
        "mode": "live", "ticker": "TSLA", "generated_at": "2026-07-10T13:00:00Z",
    })
    assert "Historical replay" not in Path(mp).read_text()


# ---- AC2: forbidden PIT families ---------------------------------------------

@pytest.mark.parametrize("key", ["P1.last", "H1.position", "P4.chain", "P8.gex"])
def test_forbidden_families_fail_in_replay(key):
    pack = {key: {"v": 1, "asof": "2025-06-01"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any(key in e for e in errors)


def test_forbidden_families_ignored_when_not_replay():
    pack = {"P1.last": {"v": 1, "asof": "2025-06-01"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=False)
    assert errors == []


def test_forbidden_in_position_pack():
    errors, _ = replay.check_pack_cutoff({}, CUT, replay=True,
                                         position_pack={"H1.shares": {"v": 100, "asof": "2025-06-01"}})
    assert any("H1.shares" in e for e in errors)


# ---- look-ahead: asof / known_at / date-valued v -----------------------------

def test_asof_after_cutoff_fails():
    pack = {"P1.price": {"v": 10, "asof": "2025-06-25"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any("after cutoff" in e for e in errors)


def test_known_at_after_cutoff_fails():
    pack = {"P3.revenue": {"v": 1e9, "asof": "2025-03-31", "known_at": "2025-07-01", "src": "sec-edgar"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any("known_at" in e for e in errors)


def test_p3_sec_edgar_missing_known_at_fails_in_replay():
    # findings guardrail #2
    pack = {"P3.revenue": {"v": 1e9, "asof": "2025-03-31", "src": "sec-edgar"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any("missing known_at" in e for e in errors)


def test_future_date_valued_v_allowed_when_covered_by_known_at():
    # a filing marker whose value is a future-looking date is OK if known_at <= cutoff
    pack = {"P3.latest_10q_filed": {"v": "2025-06-15", "unit": "date",
                                    "asof": "2025-03-31", "known_at": "2025-06-15", "src": "sec-edgar"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert errors == []


def test_future_date_valued_v_without_known_at_fails():
    pack = {"P5.next_earnings_date": {"v": "2025-07-30", "unit": "date", "asof": "2025-06-01"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any("future date" in e for e in errors)


def test_clean_pit_pack_passes():
    pack = {
        "P1.price": {"v": 148.0, "unit": "USD", "asof": "2025-06-20", "src": "schwab"},
        "P3.revenue": {"v": 1e9, "asof": "2025-03-31", "known_at": "2025-05-01", "src": "sec-edgar"},
    }
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert errors == []


# ---- P5 headlines + next_earnings --------------------------------------------

def test_headlines_missing_or_future_published_at_fail():
    pack = {"P5.headlines": {"v": [
        {"published_at": "2025-06-01", "title": "ok"},
        {"published_at": "2025-06-25", "title": "future"},
        {"title": "no-timestamp"},
    ]}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert sum("P5.headlines" in e for e in errors) == 2


def test_next_earnings_without_known_at_fails():
    pack = {"P5.next_earnings": {"v": "2025-07-30", "asof": "2025-06-01"}}
    errors, _ = replay.check_pack_cutoff(pack, CUT, replay=True)
    assert any("P5.next_earnings" in e for e in errors)


# ---- AC4: headline replay filter ---------------------------------------------

def test_filter_headlines_accept_future_missing():
    rows = [
        {"published_at": "2025-06-01", "title": "kept"},
        {"published_at": "2025-06-30", "title": "too-new", "source": "Reuters"},
        {"title": "no-ts", "source": "Bloomberg"},
    ]
    accepted, gaps = replay.filter_headlines_for_replay(rows, CUT)
    assert len(accepted) == 1 and accepted[0]["title"] == "kept"
    assert len(gaps) == 2
    # the rejected source is named in the gap message
    assert any("Reuters" in g or "too-new" in g for g in gaps)
    assert any("Bloomberg" in g or "no-ts" in g for g in gaps)


def test_filter_headlines_empty():
    assert replay.filter_headlines_for_replay(None, CUT) == ([], [])
