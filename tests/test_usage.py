"""Usage-ledger capture tests for v2.4a Flywheel L1."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _run_usage(tmp_path, *args, env=None):
    e = os.environ.copy()
    e["TRADING_RESEARCH_USAGE_LEDGER"] = str(tmp_path / "usage" / "invocations.jsonl")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "usage.py"), *args],
        capture_output=True,
        text=True,
        env=e,
    )


def _lines(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_detect_host_prefers_codex_over_nested_claude(tmp_path):
    r = _run_usage(
        tmp_path,
        "detect-host",
        "--json",
        env={"CODEX_THREAD_ID": "codex-thread", "CLAUDE_CODE_SESSION_ID": "claude-session"},
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["host"] == "codex"
    assert "CODEX_THREAD_ID" in payload["host_signals"]
    assert "nested_from_claude" in payload["host_signals"]


def test_empty_usage_ledger_override_fails_loud(tmp_path):
    r = _run_usage(
        tmp_path,
        "start",
        "--mode",
        "report",
        "--ticker",
        "NVDA",
        env={"TRADING_RESEARCH_USAGE_LEDGER": ""},
    )
    assert r.returncode == 2
    assert "TRADING_RESEARCH_USAGE_LEDGER is empty" in r.stderr
    assert not (tmp_path / "usage" / "invocations.jsonl").exists()


def test_start_writes_0600_metadata_and_prints_export(tmp_path):
    ledger = tmp_path / "usage" / "invocations.jsonl"
    run_dir = tmp_path / "runs" / "NVDA-2026-07-07-1231"
    r = _run_usage(
        tmp_path,
        "start",
        "--mode",
        "report",
        "--ticker",
        "NVDA",
        "--job-tier",
        "J1 POSITION-AWARE",
        "--position-aware",
        "--asset-class",
        "equity",
        "--run-id",
        "NVDA-2026-07-07-1231",
        "--run-dir",
        str(run_dir),
        env={"CODEX_THREAD_ID": "codex-thread"},
    )
    assert r.returncode == 0, r.stderr
    assert "export TRADING_RESEARCH_INVOCATION_ID=" in r.stdout
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    row = _lines(ledger)[0]
    assert row["event"] == "start"
    assert row["status"] == "started"
    assert row["host"] == "codex"
    assert row["ticker"] == "NVDA"
    assert row["job_tier"] == "J1"
    assert row["position_aware"] is True
    assert row["run_id"] == "NVDA-2026-07-07-1231"
    assert row["run_dir"] == str(run_dir)
    forbidden = {"qty", "shares", "market_value", "avg_cost", "cost_usd"}
    assert not forbidden.intersection(row)


def test_denylist_blocks_position_amount_metadata(tmp_path):
    r = _run_usage(
        tmp_path,
        "start",
        "--mode",
        "report",
        "--ticker",
        "AAOI",
        "--meta-json",
        json.dumps({"qty": 12, "note": "bad"}),
    )
    assert r.returncode == 2
    assert "denylisted usage metadata key: qty" in r.stderr
    assert not (tmp_path / "usage" / "invocations.jsonl").exists()


def test_end_reuses_invocation_id_and_adds_report_path(tmp_path):
    ledger = tmp_path / "usage" / "invocations.jsonl"
    run_dir = tmp_path / "runs" / "AMD-2026-07-07-1300"
    start = _run_usage(
        tmp_path,
        "start",
        "--mode",
        "report",
        "--ticker",
        "AMD",
        "--run-id",
        "AMD-2026-07-07-1300",
        "--run-dir",
        str(run_dir),
    )
    assert start.returncode == 0, start.stderr
    invocation_id = start.stdout.strip().split("=", 1)[1]
    end = _run_usage(
        tmp_path,
        "end",
        "--invocation-id",
        invocation_id,
        "--ticker",
        "AMD",
        "--run-id",
        "AMD-2026-07-07-1300",
        "--run-dir",
        str(run_dir),
        "--report-path",
        str(run_dir / "60-report.md"),
        "--exit-code",
        "0",
    )
    assert end.returncode == 0, end.stderr
    rows = _lines(ledger)
    assert [r["event"] for r in rows] == ["start", "end"]
    assert rows[1]["invocation_id"] == invocation_id
    assert rows[1]["status"] == "success"
    assert rows[1]["report_paths"] == [str(run_dir / "60-report.md")]


def test_usage_report_counts_orphan_start_as_aborted(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    rows = [
        {"v": 1, "event": "start", "invocation_id": "a", "ts": "2026-07-07T10:00:00Z",
         "host": "cursor", "source": "skill-helper", "skill": "trading-research",
         "mode": "report", "ticker": "NVDA", "status": "started"},
        {"v": 1, "event": "start", "invocation_id": "b", "ts": "2026-07-07T11:00:00Z",
         "host": "codex", "source": "skill-helper", "skill": "trading-research",
         "mode": "report", "ticker": "AMD", "status": "started"},
        {"v": 1, "event": "end", "invocation_id": "b", "ts": "2026-07-07T11:10:00Z",
         "host": "codex", "source": "skill-helper", "skill": "trading-research",
         "mode": "report", "ticker": "AMD", "status": "success", "exit_code": 0},
        {"v": 1, "event": "host_hook", "invocation_id": "claude:x", "ts": "2026-07-07T12:00:00Z",
         "host": "claude-code", "source": "claude-hook", "skill": "trading-research",
         "status": "hook_only"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "usage_report.py"), "--ledger", str(ledger), "--ttl-hours", "0"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "cursor" in r.stdout and "aborted" in r.stdout
    assert "codex" in r.stdout and "success" in r.stdout
    assert "hook_only" in r.stdout
