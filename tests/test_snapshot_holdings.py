"""Tests for snapshot_holdings.py — verbatim envelope, vendor-code passthrough,
partial-book DEGRADED, same-day no-downgrade, iCloud-safe tmp naming."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import snapshot_holdings as sh  # noqa: E402

SNAP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

# A full vendor payload — every key snaptrade_holdings.py emits, incl. per-row
# n_accounts / unrealized_pl_pct and the top-level accounts_skipped.
VENDOR = {
    "as_of": "2026-07-06T12:00:00+00:00", "total_book": 1000.0, "n_accounts": 2,
    "accounts_skipped": 0,
    "holdings": [
        {"symbol": "AAA", "kind": "equity", "qty": 10.0, "price": 50.0,
         "market_value": 500.0, "avg_cost": 40.0, "unrealized_pl": 100.0,
         "unrealized_pl_pct": 25.0, "pct_of_book": 50.0, "brokers": "Schwab",
         "n_accounts": 1},
        {"symbol": "BTC", "kind": "crypto", "qty": 0.01, "price": 50000.0,
         "market_value": 500.0, "avg_cost": None, "unrealized_pl": None,
         "unrealized_pl_pct": None, "pct_of_book": 50.0, "brokers": "Robinhood",
         "n_accounts": 1}]}


def runner(rc, out):
    return lambda: (rc, out)


def test_verbatim_envelope_golden(tmp_path):
    rc = sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, json.dumps(VENDOR)))
    assert rc == 0
    env = json.loads((tmp_path / "2026-07-06.json").read_text())
    assert env["kind"] == "holdings-snapshot" and env["schema"] == 1
    assert env["asof_date"] == "2026-07-06" and env["fetched_at"]
    # nothing dropped, nothing renamed — the vendor payload is byte-for-byte the CLI's.
    assert env["vendor"] == VENDOR
    assert env["vendor"]["accounts_skipped"] == 0
    assert env["vendor"]["holdings"][0]["unrealized_pl_pct"] == 25.0
    assert env["vendor"]["holdings"][1]["avg_cost"] is None


def test_vendor_exit_codes_pass_through(tmp_path):
    for code in (2, 3, 4):
        assert sh.main([str(tmp_path)], runner=runner(code, "")) == code
    assert not list(tmp_path.glob("*.json"))          # nothing written on failure
    assert not list(tmp_path.glob(".*"))              # no tmp left behind


def test_own_failures_collapse_to_one(tmp_path):
    assert sh.main([str(tmp_path)], runner=runner(5, "")) == 1     # unmapped code
    assert sh.main([str(tmp_path)], runner=runner(0, "")) == 1     # empty stdout
    assert sh.main([str(tmp_path)], runner=runner(0, "not json")) == 1
    assert not list(tmp_path.glob("*.json"))


def test_partial_book_degraded_still_writes(tmp_path, capsys):
    v = dict(VENDOR, accounts_skipped=2)
    rc = sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, json.dumps(v)))
    assert rc == 0
    assert (tmp_path / "2026-07-06.json").exists()
    assert "DEGRADED" in capsys.readouterr().err


def test_same_day_no_downgrade_refusal(tmp_path, capsys):
    clean = json.dumps(dict(VENDOR, accounts_skipped=0))
    partial = json.dumps(dict(VENDOR, accounts_skipped=1))
    assert sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, clean)) == 0
    # a more-partial same-day rerun must NOT replace the cleaner book
    rc = sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, partial)) == 0
    kept = json.loads((tmp_path / "2026-07-06.json").read_text())
    assert kept["vendor"]["accounts_skipped"] == 0
    assert "kept existing" in capsys.readouterr().err


def test_cleaner_book_replaces_partial(tmp_path):
    partial = json.dumps(dict(VENDOR, accounts_skipped=2))
    clean = json.dumps(dict(VENDOR, accounts_skipped=0))
    sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, partial))
    sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, clean))   # skipped 0 <= 2
    env = json.loads((tmp_path / "2026-07-06.json").read_text())
    assert env["vendor"]["accounts_skipped"] == 0


def test_tmp_name_excluded_by_selection_regex(tmp_path):
    sh.main([str(tmp_path), "2026-07-06"], runner=runner(0, json.dumps(VENDOR)))
    assert SNAP_RE.match("2026-07-06.json")
    assert not SNAP_RE.match(".2026-07-06.json.tmp")     # tmp never selected as a snapshot
    assert not list(tmp_path.glob(".*.tmp"))             # replace() removed the tmp
