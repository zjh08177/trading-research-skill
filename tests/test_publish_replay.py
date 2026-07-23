"""Tests for scripts/batch/publish_replay.py, the idempotent REPLAY-lane
publisher (mirrors scripts/batch/publish_ledger.py for the live lane, but
writes reports/replay/ + ledger-replay.jsonl instead).

ANTI-HANG: --today is fixed equal to entry_market_asof, so
add_trading_days(entry_date, horizon) > today for every default horizon
(1/5/21) -- resolve_aged_horizons therefore NEVER invokes `ledger.py resolve
--replay` as a subprocess in this file. This is a structural guarantee (the
aging check runs before any subprocess call), not a mock -- so there is no
path here that can reach schwab_bars.py or any other vendor/network call.
Decision fixtures also use mode_rating="Hold" / no_call=True as defense in
depth, since Hold/no-call rows are skipped by resolve_replay_rows even if a
resolve subprocess were somehow invoked.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import publish_replay as pr  # noqa: E402
import ledger as ledger_mod  # noqa: E402

TICKER = "TSLA"
REQUESTED_CUTOFF = "2025-06-21"
EFFECTIVE_ASOF = "2025-06-20"
ENTRY_ASOF = "2025-06-23"
GENERATED_AT = "2026-07-10T13:00:00Z"
STAMP = "1400"
RUN_ID = f"{TICKER}-{REQUESTED_CUTOFF}-{STAMP}"
TODAY = ENTRY_ASOF  # == entry_market_asof -> zero resolve subprocess calls fire

DATAPACK_MD = """# TSLA datapack

## Deterministic swing-distance facts

- P2.macd: -9.88

## Data gaps

- P4.chain: options chain unavailable pre-cutoff (replay mode)
- P8.short_interest: not point-in-time reconstructible

## Deterministic swing-distance facts

placeholder to confirm section-boundary stops at the next heading
"""

REPORT_MD = """# TSLA — Historical replay

**Historical replay**

## Thesis

MACD -9.88 [P2.macd] confirms consolidation; no directional call issued.

VERDICT: Hold | CONVICTION: 7
"""

DECISION_JSON = {
    "decision": "no-call", "mode": 3, "mode_label": "Hold",
    "median_notch": 3.0, "mean_notch": 3, "spread": 0, "mean_conviction": 7,
    "n_valid": 2, "n_target": 3,
    "judge_mix": ["cursor-session-default", "cursor-session-default"],
    "malformed": [],
}

VOTE_MD = "VERDICT: Hold | CONVICTION: 7 | WHY: consolidation, no edge.\n"


def _make_run_dir(tmp_path):
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    scope = {
        "mode": "replay", "ticker": TICKER, "asset_class": "equity",
        "requested_cutoff": REQUESTED_CUTOFF,
        "effective_market_asof": EFFECTIVE_ASOF,
        "entry_market_asof": ENTRY_ASOF, "generated_at": GENERATED_AT,
        "conservative_fallback": False, "job": "J1-replay",
    }
    (run_dir / "00-scope.json").write_text(json.dumps(scope))
    (run_dir / "10-datapack.md").write_text(DATAPACK_MD)
    (run_dir / "60-report.md").write_text(REPORT_MD)
    (run_dir / "55-decision.json").write_text(json.dumps(DECISION_JSON))
    votes_dir = run_dir / "50-votes"
    votes_dir.mkdir()
    (votes_dir / "vote-1.md").write_text(VOTE_MD)
    (votes_dir / "vote-2.md").write_text(VOTE_MD)
    return run_dir


def _publish(run_dir, tmp_path, **kw):
    reports_root = str(tmp_path)
    ledger_path = str(tmp_path / "ledger.jsonl")
    kw.setdefault("today", TODAY)
    return pr.publish_run(str(run_dir), reports_root=reports_root,
                          ledger_path=ledger_path, wall_s=12.5, cost_usd=0.42,
                          **kw)


def test_publish_copies_report_and_writes_replay_ledger_row_isolated_from_live(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    result = _publish(run_dir, tmp_path)

    dest = tmp_path / "reports" / "replay" / TICKER / f"{RUN_ID}.md"
    assert dest.exists()
    assert dest.read_text() == REPORT_MD

    replay_ledger = tmp_path / "ledger-replay.jsonl"
    assert replay_ledger.exists()
    rows = [json.loads(ln) for ln in replay_ledger.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["evidence_type"] == "replay"
    assert row["run_id"] == RUN_ID
    assert row["ticker"] == TICKER
    assert row["requested_cutoff"] == REQUESTED_CUTOFF
    assert row["effective_market_asof"] == EFFECTIVE_ASOF
    assert row["entry_market_asof"] == ENTRY_ASOF
    assert row["mode_rating"] == "Hold"
    assert row["no_call"] is True
    assert row["distribution"] == {"StrongSell": 0, "Sell": 0, "Hold": 2, "Buy": 0, "StrongBuy": 0}
    assert row["judge_mix"] == ["cursor-session-default", "cursor-session-default"]
    assert row["cost_usd"] == 0.42
    assert row["wall_s"] == 12.5

    # live lane must never be touched
    assert not (tmp_path / "ledger.jsonl").exists()
    assert not (tmp_path / "reports" / "single-ticker").exists()
    assert result["report_path"] == f"reports/replay/{TICKER}/{RUN_ID}.md"


def test_gaps_field_carries_extracted_data_gaps_section(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _publish(run_dir, tmp_path)

    row = json.loads((tmp_path / "ledger-replay.jsonl").read_text().splitlines()[0])
    assert row["gaps"] == [
        "P4.chain: options chain unavailable pre-cutoff (replay mode)",
        "P8.short_interest: not point-in-time reconstructible",
    ]


def test_publish_is_idempotent_on_second_run(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _publish(run_dir, tmp_path)
    dest = tmp_path / "reports" / "replay" / TICKER / f"{RUN_ID}.md"
    mtime_1 = dest.stat().st_mtime_ns

    result2 = _publish(run_dir, tmp_path)

    rows = [json.loads(ln) for ln in
            (tmp_path / "ledger-replay.jsonl").read_text().splitlines() if ln.strip()]
    assert len(rows) == 1  # no duplicate row

    state = json.loads((run_dir / "75-publish-state.json").read_text())
    assert state["run_id"] == RUN_ID
    assert state["ledger_appended"] is True
    assert state["report"]["md"]["hash"] == pr._sha256(str(run_dir / "60-report.md"))

    # copy step was skipped on the second run (state hash matched, no rewrite)
    assert dest.stat().st_mtime_ns == mtime_1
    assert result2["ledger_msg"]  # still returns a status message, no crash


def test_replay_eval_written_and_source_report_byte_unchanged(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    before_hash = hashlib.sha256((run_dir / "60-report.md").read_bytes()).hexdigest()

    result = _publish(run_dir, tmp_path)

    eval_md = run_dir / "80-replay-eval.md"
    eval_json = run_dir / "80-replay-eval.json"
    assert eval_md.exists()
    assert eval_json.exists()
    payload = json.loads(eval_json.read_text())
    assert payload["run_id"] == RUN_ID
    assert payload["newly_resolved"] == []
    assert payload["previously_resolved"] == []
    assert sorted(payload["pending_horizons"]) == [1, 5, 21]
    assert "No resolved horizons yet." in eval_md.read_text()

    after_hash = hashlib.sha256((run_dir / "60-report.md").read_bytes()).hexdigest()
    assert after_hash == before_hash

    # zero resolve calls occurred -> no replay-resolved sidecar was ever created
    assert not (tmp_path / "ledger-replay-resolved.jsonl").exists()
    assert result["resolved_called"] == []
    assert sorted(result["pending"]) == [1, 5, 21]


def test_aging_gate_never_fires_resolve_subprocess_for_unaged_horizons(tmp_path, monkeypatch):
    """Structural ANTI-HANG check: resolve_aged_horizons must not even build a
    subprocess argv for an unaged horizon. Patch subprocess.run in the module
    to explode if called -- this run must never touch it, proving the aging
    check (pure add_trading_days comparison) gates the call site itself."""
    run_dir = _make_run_dir(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called for unaged horizons")

    monkeypatch.setattr(pr.subprocess, "run", _boom)
    # append still needs subprocess (ledger append is unconditional and safe --
    # no network); only guard the resolve path by keeping today == entry_asof.
    # To isolate resolve specifically, call it directly rather than through
    # publish_run (which also needs the append subprocess call).
    called, pending = pr.resolve_aged_horizons(
        TICKER, ENTRY_ASOF, str(tmp_path / "ledger.jsonl"),
        __import__("datetime").date.fromisoformat(TODAY),
        pr.DEFAULT_HORIZONS, {})
    assert called == []
    assert sorted(pending) == [1, 5, 21]
