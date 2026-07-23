"""pipeline_driver.py — scope guards, routing merge, and one offline mock run.

Every test here is OFFLINE: the driver runs with --worker-cmd-override
tests/mock_worker.sh (canned artifacts + a real receipt file) and a fixture data
pack, so no vendor, model, or network is touched and the vault ledger is never
opened.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "pipeline_driver.py"
MOCK = ROOT / "tests" / "mock_worker.sh"
DUP_MOCK = ROOT / "tests" / "mock_worker_dup_sent.sh"
ROUTING = ROOT / "tests" / "routing-default.json"
PY = sys.executable

sys.path.insert(0, str(ROOT / "scripts"))
import pipeline_driver as pd  # noqa: E402


def _run(run_dir, worker=MOCK, extra=(), env=None):
    cmd = [PY, str(DRIVER), "--ticker", "TEST", "--run-dir", str(run_dir),
           "--routing", str(ROUTING), "--worker-cmd-override", str(worker)]
    return subprocess.run(cmd + list(extra), capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


# --- scope guards (pure) ---------------------------------------------------

def _args(**over):
    ns = pd.build_arg_parser().parse_args(
        ["--ticker", "UNH", "--run-dir", "/tmp/x", "--routing", str(ROUTING)])
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize("flag", ["options", "options_only", "replay", "crypto", "batch"])
def test_out_of_scope_flags_rejected(flag):
    assert "out of scope" in pd.check_scope(_args(**{flag: True}))


def test_past_asof_rejected():
    assert "replay" in pd.check_scope(_args(asof="2020-01-02"))


def test_relative_run_dir_rejected():
    assert "absolute" in pd.check_scope(_args(run_dir="rel/dir"))


def test_multi_ticker_rejected():
    assert "single-ticker" in pd.check_scope(_args(ticker="UNH,SPY"))


def test_in_scope_invocation_accepted():
    assert pd.check_scope(_args()) is None


# --- routing merge ---------------------------------------------------------

def test_routing_overrides_only_named_keys(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"writer": "custom-writer"}))
    d = pd.Driver(_args(routing=str(path), run_dir=str(tmp_path)))
    d.load_routing()
    assert d.routing["writer"] == "custom-writer"
    assert d.routing["judges"] == pd.DEFAULT_ROUTING["judges"]


@pytest.mark.parametrize("blob", [
    {"judge": "x"},                  # unknown slot -> typo must not be ignored
    {"judges": ["a", "b"]},          # wrong panel size
    {"writer": ""},                  # empty slug
])
def test_routing_bad_input_is_bad_invocation(tmp_path, blob):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(blob))
    d = pd.Driver(_args(routing=str(path), run_dir=str(tmp_path)))
    with pytest.raises(pd.DriverError) as e:
        d.load_routing()
    assert e.value.exit_code == pd.EXIT_BAD_INVOCATION


# --- invariant 12 is mechanical, not prompt-enforced -----------------------

def test_view_dir_refuses_position_artifact(tmp_path):
    d = pd.Driver(_args(run_dir=str(tmp_path)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    (tmp_path / "15-position.json").write_text("{}")
    with pytest.raises(pd.DriverError) as e:
        d.make_view_dir("2-fund", ["15-position.json"])
    assert e.value.code == "invariant-12-violation"
    assert d.position_view_violations


def test_writer_view_may_carry_position(tmp_path):
    d = pd.Driver(_args(run_dir=str(tmp_path)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    (tmp_path / "15-position.json").write_text("{}")
    view = d.make_view_dir("6-writer", ["15-position.json"], allow_position=True)
    assert os.path.exists(os.path.join(view, "15-position.json"))
    assert not d.position_view_violations


# --- offline end-to-end ----------------------------------------------------

@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_mock_run_end_to_end(tmp_path):
    run_dir = tmp_path / "run"
    proc = _run(run_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in ("10-datapack.json", "20-analyst-fund.md", "30-debate.md",
                 "40-riskbox-block.md", "40-risk.md", "45-judge-bundle.md",
                 "50-votes/vote-1.md", "55-rating-block.md", "53-meanrev-block.md",
                 "60-report.md", "60-report.html", "70-qa-prose.txt",
                 "70-qa-final.txt", "receipts.json", "RUN-MANIFEST.md",
                 "DRIVER-STATE.json"):
        assert (run_dir / name).exists(), f"missing {name}"

    state = json.loads((run_dir / "DRIVER-STATE.json").read_text())
    assert state["exit_code"] == 0
    assert state["mode"] == "mock"
    # No stage view but the writer's ever saw the position artifact.
    holders = [v["label"] for v in state["view_dirs"] if v["contains_position"]]
    assert holders == ["6-writer"], holders
    assert state["position_views_ok"] and not state["position_view_violations"]
    # The driver emits the ledger row; it never appends it.
    assert state["ledger_appended"] is False and state["vault_copied"] is False
    assert state["ledger_row"]["ticker"] == "TEST"
    # Every worker call carries a verified receipt.
    calls = json.loads((run_dir / "receipts.json").read_text())["calls"]
    assert calls and all(c.get("receipt") for c in calls)
    assert all(c.get("stage") for c in calls)


@pytest.mark.skipif(not DUP_MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_duplicated_brief_is_quarantined_not_published(tmp_path):
    run_dir = tmp_path / "run"
    proc = _run(run_dir, worker=DUP_MOCK)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (run_dir / "20-analyst-sent-malformed.md").exists()
    assert not (run_dir / "20-analyst-sent.md").exists()

    state = json.loads((run_dir / "DRIVER-STATE.json").read_text())
    assert [q["role"] for q in state["quarantines"]] == ["analyst-sent"]
    assert any("MISSING(analyst-sent)" in g for g in state["data_gaps"])
    # The defect the gate exists for: it must NOT reach the judges...
    bundle = (run_dir / "45-judge-bundle.md").read_text()
    assert "MISSING(sent)" in bundle
    # ...and it must be disclosed in the report, mechanically.
    assert "MISSING(analyst-sent)" in (run_dir / "60-report.md").read_text()


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_no_receipt_means_the_call_did_not_happen(tmp_path):
    stub = tmp_path / "no_receipt.sh"
    stub.write_text("#!/usr/bin/env bash\ncat > /dev/null\necho '## Bull case'\n"
                    "echo 'plausible output, no receipt'\n")
    stub.chmod(0o755)
    proc = _run(tmp_path / "run", worker=stub)
    assert proc.returncode == pd.EXIT_NEEDS_ORCH
    state = json.loads((tmp_path / "run" / "DRIVER-STATE.json").read_text())
    assert state["reason"]["code"] == "stage2-empty"
    assert any("no-receipt" in r for q in state["quarantines"] for r in q["reasons"])


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_resume_skips_completed_stages(tmp_path):
    run_dir = tmp_path / "run"
    assert _run(run_dir).returncode == 0
    proc = _run(run_dir, extra=["--resume"])
    assert proc.returncode == 0
    assert proc.stdout.count("skipped") == len(pd.Driver.STAGE_PLAN)
    # A skipped stage 5 must still hand its decision forward: an all-skipped
    # resume that reported a null mode_rating/spread/n_valid was publishing a
    # ledger row that knew nothing about the rating it was shipping.
    row = json.loads((run_dir / "DRIVER-STATE.json").read_text())["ledger_row"]
    assert row["mode_rating"] and row["spread"] is not None and row["n_valid"] == 3


# --- DEFECT 1: a provisional (n=3) tally must never be published as final ----

def _escalating_fixtures(tmp_path):
    """Canned fixtures with slot 1 rigged to StrongSell, so the n=3 panel reads
    StrongSell/Hold/Buy -> spread 3 -> ensemble.decide() returns 'escalate' and
    the round-1 block is rendered 'provisional, escalating to N=5'."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(ROOT / "tests" / "fixtures" / "driver", fixtures)
    vote1 = fixtures / "vote-1.md"
    vote1.write_text(vote1.read_text().replace("VERDICT: Hold |",
                                               "VERDICT: StrongSell |"))
    return fixtures


def _crash_after_first_judge_round(run_dir):
    """Run stages 1-4 and ONLY the first judge round, then abandon the run —
    what a SIGKILL between the n=3 tally and the n=5 escalation leaves behind
    (no DRIVER-STATE.json, no completed stage 5). Returns the n=3 decision."""
    args = pd.build_arg_parser().parse_args(
        ["--ticker", "TEST", "--run-dir", str(run_dir), "--routing", str(ROUTING),
         "--worker-cmd-override", str(MOCK)])
    d = pd.Driver(args)
    d.load_routing()
    d.run_dir.mkdir(parents=True, exist_ok=True)
    d.tmp_root = tempfile.mkdtemp(prefix="trdrv-views-crashtest-")
    try:
        d.stage1()
        d.stage2()
        d.stage3()
        d.stage4()
        d.stage_start("5", "ensemble")
        bundle = d._assemble_bundle()
        d.write("45-judge-bundle.md", bundle)
        d.p("50-votes").mkdir(parents=True, exist_ok=True)
        _, decision = d._judge_round(
            slots=[1, 2, 3], models=d.routing["judges"], bundle=bundle,
            bundle_sha=pd.sha256(bundle), n_target=3)
    finally:
        d.cleanup_views()
    return decision


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_resume_after_crash_mid_escalation_reruns_stage5(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    monkeypatch.setenv("MOCK_WORKER_FIXTURES", str(_escalating_fixtures(tmp_path)))

    decision = _crash_after_first_judge_round(run_dir)
    assert decision["decision"] == "escalate" and decision["spread"] == 3

    # THE INVARIANT. Round 1's block literally reads "provisional, escalating to
    # N=5"; the file the report inserts verbatim — and that --resume stats as
    # stage 5's sentinel — must never be holding it.
    block = run_dir / "55-rating-block.md"
    assert not (block.exists() and "provisional" in block.read_text().lower())
    assert not (run_dir / "DRIVER-STATE.json").exists()      # a real SIGKILL
    # This driver's implementation of it: the provisional round lives elsewhere.
    assert "provisional, escalating to N=5" in (run_dir / pd.PROVISIONAL_TALLY).read_text()
    assert not block.exists()

    proc = _run(run_dir, extra=["--resume"],
                env={"MOCK_WORKER_FIXTURES": str(tmp_path / "fixtures")})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Stage 5 re-ENTERED (the whole point) — stages 1-4 still skipped.
    assert "ensemble-start" in proc.stdout and "ensemble-skipped" not in proc.stdout
    assert "datapack-skipped" in proc.stdout

    # The escalation actually completed: five votes, an N=5 tally, no provisional
    # headline anywhere in the block the report inserts verbatim.
    assert len(list((run_dir / "50-votes").glob("vote-*.md"))) == 5
    final_block = block.read_text()
    assert "provisional" not in final_block.lower()
    assert "target 5" in final_block
    decision = json.loads((run_dir / "55-decision.json").read_text())
    assert decision["n_target"] == 5 and decision["n_valid"] == 5
    assert decision["decision"] not in ("escalate", "backfill")

    state = json.loads((run_dir / "DRIVER-STATE.json").read_text())
    assert state["exit_code"] == 0 and state["ensemble"]["n_valid"] == 5
    row = state["ledger_row"]
    assert state["ledger_row_error"] is None
    for key in ("mode_rating", "spread", "n_valid"):
        assert row[key] is not None, key
    assert row["n_valid"] == 5
    assert json.loads((run_dir / "80-ledger-row.json").read_text()) == row


def _plant_stage5(run_dir, decision):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "55-rating-block.md").write_text(
        "### Ensemble Rating: **Hold** — provisional, escalating to N=5\n")
    (run_dir / "55-decision.json").write_text(json.dumps(decision))


@pytest.mark.parametrize("decision", ["escalate", "backfill"])
def test_stage5_not_done_while_the_tally_is_provisional(tmp_path, decision):
    """Belt-and-braces for the file-name fix: even handed a rating block written
    by an older driver, --resume must re-enter stage 5 when the decision on disk
    is not final."""
    run_dir = tmp_path / "run"
    _plant_stage5(run_dir, {"decision": decision, "n_target": 3, "n_valid": 3})
    d = pd.Driver(_args(run_dir=str(run_dir)))
    assert d.stage_done("stage5") is False


def test_stage5_done_and_rehydrated_when_the_tally_is_final(tmp_path):
    run_dir = tmp_path / "run"
    final = {"decision": "publish", "n_target": 3, "n_valid": 3, "spread": 1,
             "mode_label": "Hold"}
    _plant_stage5(run_dir, final)
    d = pd.Driver(_args(run_dir=str(run_dir)))
    assert d.stage_done("stage5") is True
    d._resume_rehydrate("stage5")
    assert d.ensemble == final


def test_stage5_not_done_when_the_decision_is_missing_or_unreadable(tmp_path):
    run_dir = tmp_path / "run"
    _plant_stage5(run_dir, {"decision": "publish", "n_target": 3})
    d = pd.Driver(_args(run_dir=str(run_dir)))
    (run_dir / "55-decision.json").unlink()
    assert d.stage_done("stage5") is False          # cannot prove it was final
    (run_dir / "55-decision.json").write_text("{not json")
    assert d.stage_done("stage5") is False


def test_ledger_row_refuses_a_null_headline(tmp_path):
    """The compounding path: ledger.py checks key PRESENCE only, and the
    orchestrator appends on exit 0 — so a row with no rating must die here."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "55-rating-block.md").write_text("### Ensemble Rating: **Hold**\n")
    d = pd.Driver(_args(run_dir=str(run_dir)))
    d.ensemble = {}                                  # stage 5 never handed one over
    with pytest.raises(pd.DriverError) as e:
        d._ledger_row()
    assert e.value.code == "ledger-row-incomplete"
    assert e.value.exit_code == pd.EXIT_NEEDS_ORCH
    assert "mode_rating" in e.value.detail


# --- DEFECT 2: view dirs never outlive the run ------------------------------

def _view_paths(run_dir):
    state = json.loads((run_dir / "DRIVER-STATE.json").read_text())
    return state, [v["path"] for v in state["view_dirs"]]


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_view_dirs_are_removed_after_a_clean_run(tmp_path):
    run_dir = tmp_path / "run"
    assert _run(run_dir).returncode == 0
    state, paths = _view_paths(run_dir)
    assert paths and state["view_dirs_removed"] is True
    assert [p for p in paths if os.path.exists(p)] == []
    # The writer's view is the one that carries real holdings; it must be gone.
    writer = [v for v in state["view_dirs"] if v["contains_position"]]
    assert writer and not any(os.path.exists(v["path"]) for v in writer)


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_view_dirs_are_removed_on_the_failure_path(tmp_path):
    """exit 10 is not an excuse to leak position data into system temp."""
    stub = tmp_path / "no_receipt.sh"
    stub.write_text("#!/usr/bin/env bash\ncat > /dev/null\necho '## Bull case'\n"
                    "echo 'plausible output, no receipt'\n")
    stub.chmod(0o755)
    run_dir = tmp_path / "run"
    assert _run(run_dir, worker=stub).returncode == pd.EXIT_NEEDS_ORCH
    state, paths = _view_paths(run_dir)
    assert paths and state["view_dirs_removed"] is True
    assert [p for p in paths if os.path.exists(p)] == []


@pytest.mark.skipif(not MOCK.exists() or not shutil.which("bash"),
                    reason="mock worker unavailable")
def test_keep_views_retains_them_and_says_so(tmp_path):
    run_dir = tmp_path / "run"
    proc = _run(run_dir, extra=["--keep-views"])
    assert proc.returncode == 0
    state, paths = _view_paths(run_dir)
    assert state["view_dirs_removed"] is False
    assert all(os.path.exists(p) for p in paths)
    assert "--keep-views" in proc.stderr and "15-position" in proc.stderr
    shutil.rmtree(os.path.dirname(paths[0]), ignore_errors=True)


# --- DEFECT 3: an unreadable rating block is loud, not an all-zero row -------

def test_unreadable_rating_block_is_not_a_zero_distribution(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    d = pd.Driver(_args(run_dir=str(run_dir)))
    d.ensemble = {"decision": "publish", "mode_label": "Hold", "spread": 1,
                  "n_valid": 3, "n_target": 3}
    with pytest.raises(pd.DriverError) as e:
        d._ledger_row()                              # 55-rating-block.md absent
    assert e.value.code == "rating-block-unreadable"
