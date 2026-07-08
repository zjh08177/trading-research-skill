"""Read-only --evolve retro tests for v2.4a Flywheel L2."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _run_dir(root, run_id, body="Report body\nDATA GAP: P5 missing\n"):
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "60-report.md").write_text(body)
    return d


def test_evolve_indexes_real_corpus_by_run_id_not_ledger_report_path(tmp_path):
    runs = tmp_path / "runs"
    _run_dir(runs, "NVDA-2026-07-05-1300")
    _run_dir(runs, "AMD-2026-07-06-0900", "Clean report\n")
    _run_dir(runs, "UNH-2026-07-07-1100", "Unledgered report\n")
    ledger = tmp_path / "ledger.jsonl"
    _jsonl(ledger, [
        {"run_id": "NVDA-2026-07-05-1300", "ticker": "NVDA", "date_utc": "2026-07-05",
         "mode_rating": "Buy", "distribution": {"Buy": 3}, "spread": 0,
         "no_call": False, "gaps": ["P5 MISSING(news)"], "report_path": "bogus/nvda.md",
         "wall_s": 600, "cost_usd": 2.4},
        {"run_id": "AMD-2026-07-06-0900", "ticker": "AMD", "date_utc": "2026-07-06",
         "mode_rating": "Hold", "distribution": {"Hold": 2, "Buy": 1}, "spread": 2,
         "no_call": False, "gaps": [], "report_path": "also/bogus.md",
         "wall_s": 1200, "cost_usd": 3.5},
        {"run_id": "FUTURE-2026-07-08-0900", "ticker": "FUTURE", "date_utc": "2026-07-08",
         "mode_rating": "Sell", "distribution": {"Sell": 3}, "spread": 0,
         "no_call": False, "gaps": [], "report_path": "future.md"},
    ])
    usage = tmp_path / "usage.jsonl"
    _jsonl(usage, [
        {"v": 1, "event": "end", "invocation_id": "u1", "ts": "2026-07-07T18:00:00Z",
         "host": "cursor", "source": "skill-helper", "skill": "trading-research",
         "mode": "report", "ticker": "UNH", "run_id": "UNH-2026-07-07-1100",
         "run_dir": str(runs / "UNH-2026-07-07-1100"), "status": "success",
         "report_paths": [str(runs / "UNH-2026-07-07-1100" / "60-report.md")]},
        {"v": 1, "event": "end", "invocation_id": "evolve", "ts": "2026-07-07T19:00:00Z",
         "host": "codex", "source": "skill-helper", "skill": "trading-research",
         "mode": "evolve", "status": "success"},
    ])
    out = tmp_path / "out"
    vault_evolve = tmp_path / "reports" / "evolve"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evolve.py"),
         "--usage-ledger", str(usage),
         "--ledger", str(ledger),
         "--runs-dir", str(runs),
         "--outdir", str(out),
         "--vault-evolve-dir", str(vault_evolve),
         "--before", "2026-07-08"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr

    index = json.loads((out / "10-corpus-index.json").read_text())
    by_id = {row["run_id"]: row for row in index["runs"]}
    assert by_id["NVDA-2026-07-05-1300"]["report_path"] == str(runs / "NVDA-2026-07-05-1300" / "60-report.md")
    assert by_id["NVDA-2026-07-05-1300"]["join_status"] == "ledgered"
    assert by_id["UNH-2026-07-07-1100"]["join_status"] == "unledgered"
    assert "FUTURE-2026-07-08-0900" not in by_id
    assert all(row.get("mode") != "evolve" for row in index["runs"])

    signals = json.loads((out / "20-signals.json").read_text())
    assert signals["calibration"]["status"] == "dormant"
    assert signals["coverage"]["total_runs"] == 3
    assert signals["cost_latency"]["n"] == 2
    assert signals["cost_latency"]["wall_s"]["max"] == 1200
    assert signals["cost_latency"]["cost_usd"]["sum"] == 5.9
    gap = signals["clusters"]["gaps"][0]
    assert gap["n"] == 1 and gap["evidence_run_ids"] == ["NVDA-2026-07-05-1300"]

    retro = (out / "30-retro.md").read_text()
    assert "Coverage" in retro
    assert "P5 MISSING(news)" in retro
    assert "Cost / latency" in retro
    assert "wall_s max 1200" in retro
    assert "cost_usd sum 5.90" in retro
    assert "calibration dormant" in retro.lower()
    assert "hit-rate" not in retro.lower()
    assert "mean alpha" not in retro.lower()
    vault_files = list(vault_evolve.glob("evolve-*.md"))
    assert len(vault_files) == 1
    assert vault_files[0].read_text() == retro


def test_evolve_preserves_usage_host_mode_status_for_ledgered_run(tmp_path):
    runs = tmp_path / "runs"
    _run_dir(runs, "CRWD-2026-07-07-1200")
    ledger = tmp_path / "ledger.jsonl"
    _jsonl(ledger, [
        {"run_id": "CRWD-2026-07-07-1200", "ticker": "CRWD", "date_utc": "2026-07-07",
         "mode_rating": "Hold", "distribution": {"Hold": 3}, "spread": 0,
         "no_call": True, "gaps": ["options_skipped"], "report_path": "bogus.md",
         "wall_s": 900, "cost_usd": 1.25},
    ])
    usage = tmp_path / "usage.jsonl"
    _jsonl(usage, [
        {"v": 1, "event": "end", "invocation_id": "u-crwd", "ts": "2026-07-07T19:00:00Z",
         "host": "cursor", "source": "skill-helper", "skill": "trading-research",
         "mode": "options", "ticker": "CRWD", "run_id": "CRWD-2026-07-07-1200",
         "run_dir": str(runs / "CRWD-2026-07-07-1200"), "status": "failed",
         "report_paths": [str(runs / "CRWD-2026-07-07-1200" / "60-report.md")]},
    ])
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evolve.py"),
         "--usage-ledger", str(usage), "--ledger", str(ledger),
         "--runs-dir", str(runs), "--outdir", str(out), "--before", "2026-07-08"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    index = json.loads((out / "10-corpus-index.json").read_text())
    row = index["runs"][0]
    assert row["join_status"] == "ledgered"
    assert row["host"] == "cursor"
    assert row["mode"] == "options"
    assert row["status"] == "failed"
    assert row["ledger"]["no_call"] is True

    signals = json.loads((out / "20-signals.json").read_text())
    assert signals["coverage"]["by_host"] == {"cursor": 1}
    assert signals["coverage"]["by_mode"] == {"options": 1}
    assert signals["coverage"]["by_status"] == {"failed": 1}
    assert signals["clusters"]["no_call"][0]["evidence_run_ids"] == ["CRWD-2026-07-07-1200"]


def test_evolve_warns_on_duplicate_run_id_and_handles_batch_parent(tmp_path):
    runs = tmp_path / "runs"
    _run_dir(runs, "AAPL-2026-07-05-1300")
    ledger = tmp_path / "ledger.jsonl"
    _jsonl(ledger, [
        {"run_id": "AAPL-2026-07-05-1300", "ticker": "AAPL", "date_utc": "2026-07-05",
         "mode_rating": "Buy", "distribution": {"Buy": 3}, "spread": 0,
         "no_call": False, "gaps": [], "report_path": "a.md"},
        {"run_id": "AAPL-2026-07-05-1300", "ticker": "AAPL", "date_utc": "2026-07-05",
         "mode_rating": "Buy", "distribution": {"Buy": 3}, "spread": 0,
         "no_call": False, "gaps": [], "report_path": "a2.md"},
    ])
    usage = tmp_path / "usage.jsonl"
    _jsonl(usage, [
        {"v": 1, "event": "end", "invocation_id": "batch", "ts": "2026-07-05T13:00:00Z",
         "host": "claude-code", "source": "skill-helper", "skill": "trading-research",
         "mode": "batch", "status": "success", "child_run_ids": ["AAPL-2026-07-05-1300"]},
    ])
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evolve.py"),
         "--usage-ledger", str(usage), "--ledger", str(ledger),
         "--runs-dir", str(runs), "--outdir", str(out), "--before", "2026-07-06"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "duplicate run_id: AAPL-2026-07-05-1300" in r.stderr
    index = json.loads((out / "10-corpus-index.json").read_text())
    assert any(row["join_status"] == "batch-parent" for row in index["runs"])
