"""tests/test_profiler.py -- the profiler feature (design-proposal.md):
trace/trace.jsonl (L1), trace/summary.json (L2) and scripts/trace.py (L3).

Every test here is OFFLINE, same posture as test_pipeline_driver.py: canned
mock workers, a fixture data pack, no vendor/model/network call.

The S1 deadlock-proof test is the load-bearing one -- it is what stands
between "profiler" and "profiler that can hang the pipeline it profiles".
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "pipeline_driver.py"
TRACE_CLI = ROOT / "scripts" / "trace.py"
MOCK = ROOT / "tests" / "mock_worker.sh"
BIGOUTPUT = ROOT / "tests" / "mock_worker_bigoutput.sh"
SLOW = ROOT / "tests" / "mock_worker_slow.sh"
WRAPPER_LAG = ROOT / "tests" / "mock_worker_wrapper_lag.sh"
ORPHAN = ROOT / "tests" / "mock_worker_orphan.sh"
STALL_RESUME = ROOT / "tests" / "mock_worker_stall_resume.sh"
BADBYTES = ROOT / "tests" / "mock_worker_badbytes.sh"
ROUTING = ROOT / "tests" / "routing-default.json"
PY = sys.executable

sys.path.insert(0, str(ROOT / "scripts"))
import pipeline_driver as pd  # noqa: E402
import trace as trace_mod  # noqa: E402

BASH_OK = shutil.which("bash") is not None


def _run(run_dir, worker=MOCK, extra=(), env=None):
    cmd = [PY, str(DRIVER), "--ticker", "TEST", "--run-dir", str(run_dir),
           "--routing", str(ROUTING), "--worker-cmd-override", str(worker)]
    return subprocess.run(cmd + list(extra), capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def _args(**over):
    ns = pd.build_arg_parser().parse_args(
        ["--ticker", "TEST", "--run-dir", "/tmp/x", "--routing", str(ROUTING)])
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _trace_lines(run_dir):
    path = Path(run_dir) / "trace" / "trace.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- S1: the deadlock-proof drill (REQUIRED by the task) -------------------


@pytest.mark.skipif(not BIGOUTPUT.exists() or not BASH_OK,
                    reason="bigoutput fixture unavailable")
def test_deadlock_proof_large_stdout_and_stderr_does_not_hang(tmp_path):
    """S1's core promise: the reader threads drain stdout AND stderr
    continuously and unconditionally until EOF. A worker that writes well
    over the ~64 KB default OS pipe buffer on BOTH streams and only then
    exits must not be able to block the child and hang `_run_once` forever.

    Run on a background thread with a bounded join() so a real regression
    (a deadlock) fails this test loudly instead of hanging the whole suite."""
    d = pd.Driver(_args(run_dir=str(tmp_path), worker_cmd_override=str(BIGOUTPUT)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    view = d.make_view_dir("deadlock-test", [])

    box = {}

    def _call():
        box["res"] = d._run_once("bull", "mock-model",
                                 "ROLE: bull\nRUN: TEST\nplease produce output",
                                 view, 30, 1, {"stage": "bull"})

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=25)
    assert not t.is_alive(), ("driver._run_once did not return within 25s -- "
                              "looks like a pipe-drain deadlock")
    res = box["res"]
    assert res.get("ok") is True, res
    assert res.get("exit_code") == 0
    assert len(res["text"].encode("utf-8")) > 65536, "stdout was truncated/lost"
    assert len(res.get("stderr_tail") or "") > 0
    assert res.get("truncated") is False, "a complete drain must not be flagged truncated"


def _run_once_bounded(driver, worker_role="bull", timeout=25, **over):
    """`_run_once` on a background thread with a bounded join, so a drain
    regression fails the test loudly instead of hanging the whole suite."""
    box = {}
    kwargs = {"role": worker_role, "model": "mock-model",
              "prompt": f"ROLE: {worker_role}\nRUN: TEST\nplease produce output",
              "timeout_s": 30, "attempt": 1, "meta": {"stage": worker_role}}
    kwargs.update(over)

    def _call():
        box["res"] = driver._run_once(
            kwargs["role"], kwargs["model"], kwargs["prompt"], kwargs["view_dir"],
            kwargs["timeout_s"], kwargs["attempt"], kwargs["meta"])

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)
    assert not t.is_alive(), f"driver._run_once did not return within {timeout}s"
    return box["res"]


def _driver_with(tmp_path, worker):
    d = pd.Driver(_args(run_dir=str(tmp_path), worker_cmd_override=str(worker)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    return d


# --- DEFECT 1: a bounded join must never silently truncate ------------------


@pytest.mark.skipif(not ORPHAN.exists() or not BASH_OK,
                    reason="orphan fixture unavailable")
def test_orphaned_grandchild_holding_the_pipe_is_declared_not_silently_truncated(
        tmp_path, monkeypatch):
    """DEFECT 1. A grandchild that inherits the pipe write-end and outlives the
    wrapper leaves the reader thread blocked in read() at join timeout. The old
    code then read the buffers anyway -- so a report could ship silently short,
    or a receipt line that WAS written could be missed and the call misclassified
    `no-receipt`, with no error, no warning and no trace event.

    The drain must therefore either complete or be DECLARED incomplete."""
    monkeypatch.setattr(pd, "DRAIN_JOIN_TIMEOUT_S", 1)
    monkeypatch.setenv("MOCK_WORKER_ORPHAN_SLEEP_S", "8")
    d = _driver_with(tmp_path, ORPHAN)
    view = d.make_view_dir("orphan-test", [])

    res = _run_once_bounded(d, view_dir=view, timeout=20)

    assert res.get("truncated") is True, res
    assert res.get("truncated_streams"), res
    assert set(res["truncated_streams"]) <= {"stdout", "stderr"}
    # Declared, not degraded: an unprovable drain is a failure, which
    # run_worker retries exactly like any other infrastructure failure.
    assert res.get("ok") is False
    assert res.get("failure") == "output-truncated", res
    assert "cannot be proven complete" in res.get("detail", "")

    # ...and it is loud on all three surfaces, not just the return value.
    assert any("INCOMPLETE" in n for n in d.notes), d.notes
    events = _trace_lines(tmp_path)
    assert [e for e in events if e["ev"] == "worker-truncated"], events
    anom = [e for e in events if e["ev"] == "anomaly" and e["kind"] == "output-truncated"]
    assert anom, events
    # and it survives into the thing an operator actually reads.
    summary = trace_mod.summarize(events)
    assert "output-truncated" in [a["kind"] for a in summary["anomalies"]]


# --- DEFECT 3: a bad byte must not abort the drain --------------------------


@pytest.mark.skipif(not BADBYTES.exists() or not BASH_OK,
                    reason="badbytes fixture unavailable")
def test_invalid_utf8_does_not_abort_the_drain_or_lose_output(tmp_path):
    """DEFECT 3. `Popen(text=True)` decodes inside the reader thread, so an
    invalid byte sequence raised UnicodeDecodeError (a ValueError) there; the
    old `except (ValueError, OSError): pass` stopped the drain and the caller
    could not tell it from a clean EOF. Everything after the bad bytes was lost
    silently -- including the receipt line on stderr."""
    d = _driver_with(tmp_path, BADBYTES)
    view = d.make_view_dir("badbytes-test", [])

    res = _run_once_bounded(d, view_dir=view)

    assert res.get("ok") is True, res
    assert res.get("truncated") is False, res
    # the receipt line came AFTER the bad bytes on stderr
    assert res.get("receipt_path"), res
    # ...and so did the artifact tail on stdout
    assert "TAIL-MARKER-AFTER-BAD-BYTES" in res["text"], res["text"]
    assert res["text"].rstrip().endswith(
        "every byte written after the bad sequence must survive"), res["text"]
    # the loss is replaced, visibly, never dropped
    assert "�" in res["text"]


# --- DEFECT 5: activity is observed per chunk, not per 4 KB -----------------


@pytest.mark.skipif(not STALL_RESUME.exists() or not BASH_OK,
                    reason="stall-resume fixture unavailable")
def test_stall_then_resume_fires_worker_resume_and_keeps_ttfa_truthful(
        tmp_path, monkeypatch):
    """DEFECT 5 (and the missing assertion the implementer admitted to).

    `stream.read(4096)` on a text pipe is read-until-full: it returns only at
    4096 bytes or EOF. So `last_activity` moved in 4 KB steps, `worker-resume`
    had never fired in ANY test (0 occurrences repo-wide -- the large-output
    fixture does not cover it, because it never goes silent), and `ttfa_ms`
    reported the END of a sub-4 KB call instead of its first byte.

    This isolates a stall-then-resume sequence: ~20 bytes at t=0, silence, then
    ~20 more bytes. Under the old reader NEITHER write is observed before EOF."""
    monkeypatch.setattr(pd, "STALL_AFTER_S", 1)
    monkeypatch.setenv("MOCK_WORKER_STALL_SLEEP_S", "4")
    monkeypatch.setenv("MOCK_WORKER_STALL_ROLE", "risk")
    d = _driver_with(tmp_path, STALL_RESUME)
    view = d.make_view_dir("stall-resume-test", [])

    res = d.run_worker("risk", "mock-model", "ROLE: risk\nRUN: TEST\nprompt",
                       view, 30, {"stage": "risk"})
    assert res.get("ok") is True, res

    events = _trace_lines(tmp_path)
    stalls = [e for e in events if e["ev"] == "worker-stall"]
    resumes = [e for e in events if e["ev"] == "worker-resume"]
    assert stalls, events
    assert resumes, "worker-resume never fired — activity is still only " \
                    "observed per full read buffer"
    assert resumes[0]["role"] == "risk"
    assert resumes[0]["stalled_s"] >= 1, resumes

    # ttfa_ms is spawn -> FIRST output byte (a ~20-byte stderr chirp at t=0),
    # not the end of a 4+ second call.
    assert res.get("ttfa_ms") is not None
    assert res["ttfa_ms"] < 1500, (
        f"ttfa_ms={res['ttfa_ms']} — that is the end of the call, not its "
        f"first byte; the reader is still read-until-full")

    # a call that is not stalled at the end is not flagged truncated
    assert res.get("truncated") is False


# --- DEFECT 4: a stall has to reach summary.json ----------------------------


@pytest.mark.skipif(not STALL_RESUME.exists() or not BASH_OK,
                    reason="stall-resume fixture unavailable")
def test_a_recovered_stall_surfaces_as_an_anomaly_in_summary_json(
        tmp_path, monkeypatch):
    """DEFECT 4. The L1 schema defines `anomaly.kind: worker-stalled`, and L2
    copies `anomaly` events verbatim -- but nothing emitted one, and
    `summarize()` did not recognise the raw `worker-stall`/`worker-resume`
    events either, so it dropped them entirely. A worker that stalled and then
    recovered within budget left NO anomaly in the artifact you actually read,
    defeating S1 (the spec's headline feature)."""
    monkeypatch.setattr(pd, "STALL_AFTER_S", 1)
    monkeypatch.setenv("MOCK_WORKER_STALL_SLEEP_S", "4")
    monkeypatch.setenv("MOCK_WORKER_STALL_ROLE", "risk")
    d = _driver_with(tmp_path, STALL_RESUME)
    view = d.make_view_dir("stall-summary-test", [])

    res = d.run_worker("risk", "mock-model", "ROLE: risk\nRUN: TEST\nprompt",
                       view, 30, {"stage": "risk"})
    assert res.get("ok") is True, res

    events = _trace_lines(tmp_path)
    summary = trace_mod.summarize(events)
    stalled = [a for a in summary["anomalies"] if a["kind"] == "worker-stalled"]
    assert stalled, summary["anomalies"]
    assert stalled[0]["role"] == "risk"
    assert stalled[0]["ms"] >= 1000, stalled
    assert "resumed" in (stalled[0]["detail"] or "")
    # exactly one anomaly per stall episode -- L1's event and L2's synthesis
    # must not both fire for the same episode.
    assert len(stalled) == len([e for e in events if e["ev"] == "worker-stall"])


def test_summarize_synthesizes_a_stall_that_never_resumed(tmp_path):
    """DEFECT 4, criterion-9 half: a run killed mid-stall never got to emit its
    `anomaly{kind: worker-stalled}`, so `summarize()` has to synthesize one from
    the orphaned `worker-stall` event. That is the most informative stall there
    is -- it is the one that ended the run."""
    events = [
        {"t": "2026-07-21T00:00:00.000+00:00", "ev": "run-start", "schema": 1,
         "run_id": "TEST-1", "ticker": "TEST"},
        {"t": "2026-07-21T00:00:01.000+00:00", "ev": "stage-start",
         "stage": "6", "name": "writer"},
        {"t": "2026-07-21T00:03:21.000+00:00", "ev": "worker-stall",
         "role": "writer", "attempt": 1, "stage": "writer",
         "age_s": 200, "budget_left_s": 400},
    ]
    summary = trace_mod.summarize(events)
    stalled = [a for a in summary["anomalies"] if a["kind"] == "worker-stalled"]
    assert len(stalled) == 1, summary["anomalies"]
    assert stalled[0]["role"] == "writer"
    assert stalled[0]["ms"] == 200_000
    assert "died mid-stall" in stalled[0]["detail"]


def test_summarize_does_not_double_count_a_stall_the_driver_already_declared():
    """The synthesis above must not duplicate an episode L1 already covered."""
    events = [
        {"t": "2026-07-21T00:00:00.000+00:00", "ev": "worker-stall",
         "role": "writer", "attempt": 1, "age_s": 200, "budget_left_s": 400},
        {"t": "2026-07-21T00:01:00.000+00:00", "ev": "anomaly",
         "kind": "worker-stalled", "role": "writer", "attempt": 1,
         "stage": "writer", "ms": 200_000, "detail": "resumed"},
    ]
    stalled = [a for a in trace_mod.summarize(events)["anomalies"]
               if a["kind"] == "worker-stalled"]
    assert len(stalled) == 1, stalled
    assert stalled[0]["detail"] == "resumed"


# --- DEFECT 2: instrumentation I/O must never kill the run ------------------


def test_trace_emit_swallows_io_errors_loudly_and_never_raises(tmp_path):
    """DEFECT 2. `Trace.emit()` is called unguarded from ~13 hot-path sites; a
    disk-full/quota/permission error there propagated to `_run_driver`'s
    catch-all and turned a fully-successful run into `driver-crash`. It must
    warn once, latch a flag, and keep going -- never silently, never fatal."""
    d = pd.Driver(_args(run_dir=str(tmp_path)))

    class DiskFull:
        def write(self, _line):
            raise OSError(28, "No space left on device")

        def flush(self):
            pass

        def close(self):
            pass

    d.trace._fh = DiskFull()
    for i in range(3):
        d.trace.emit("stage-start", stage=str(i), name="x")   # must not raise

    assert d.trace.degraded is True
    assert d.trace.dropped_events == 3
    assert "No space left on device" in d.trace.degraded_reason
    # loud, and exactly once -- a full disk stays full; 200 identical warnings
    # would bury the real one.
    notes = [n for n in d.notes if "trace instrumentation degraded" in n]
    assert len(notes) == 1, d.notes
    assert "the run CONTINUES" in notes[0]


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_unwritable_trace_path_degrades_but_the_run_still_exits_0(tmp_path):
    """DEFECT 2, end to end. `run_dir/trace` is a FILE, so every trace write
    fails (mkdir + open both raise). The run is otherwise perfect and must exit
    0 with the degradation DECLARED in DRIVER-STATE.json -- not exit 10 with
    reason `driver-crash`."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "trace").write_text("not a directory\n", encoding="utf-8")

    proc = _run(run_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    state = json.loads((run_dir / "DRIVER-STATE.json").read_text())
    assert state["status"].startswith("published-ready"), state["status"]
    assert state.get("reason") is None
    assert state["trace_degraded"] is True
    assert state["trace_events_dropped"] > 0
    assert state["trace_degraded_reason"]
    assert any("trace instrumentation degraded" in n for n in state["notes"])
    assert "trace instrumentation degraded" in proc.stderr
    assert (run_dir / "trace").is_file()          # still a file: nothing forced


# --- criteria 1-3: trace exists, census agrees, split integrity ------------


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_mock_run_trace_and_summary_shape(tmp_path):
    run_dir = tmp_path / "run"
    proc = _run(run_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    events = _trace_lines(run_dir)
    assert events, "trace.jsonl is empty"
    assert events[0]["ev"] == "run-start"
    assert events[0]["schema"] == 1
    assert events[-1]["ev"] == "run-end"

    summary_path = run_dir / "trace" / "summary.json"
    summary = json.loads(summary_path.read_text())
    assert summary["schema"] == 1
    assert summary["driver_version"]
    assert summary["routing_sha256"]
    assert summary["mode"] == "mock"

    # criterion 2: census agreement -- receipts.json calls vs worker-end count.
    receipts = json.loads((run_dir / "receipts.json").read_text())
    n_worker_end = sum(1 for e in events if e["ev"] == "worker-end")
    assert len(receipts["calls"]) == n_worker_end

    # criterion 3: split integrity.
    for e in events:
        if e["ev"] != "worker-end":
            continue
        if e.get("receipt_path") is not None:
            assert e["wrapper_ms"] >= 0, e
            assert e["model_ms"] + e["wrapper_ms"] == e["wall_ms"], e
        else:
            assert e["model_ms"] is None and e["wrapper_ms"] is None, e


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_no_receipt_call_still_emits_worker_end_with_null_split(tmp_path):
    """A `_run_once` failure branch (no-receipt) is a `worker-end` too --
    census parity (criterion 2) holds across FAILED calls, not just accepted
    ones, and model_ms/wrapper_ms are null, never 0."""
    stub = tmp_path / "no_receipt.sh"
    stub.write_text("#!/usr/bin/env bash\ncat > /dev/null\necho '## Bull case'\n"
                    "echo 'plausible output, no receipt'\n")
    stub.chmod(0o755)
    run_dir = tmp_path / "run"
    proc = _run(run_dir, worker=stub)
    assert proc.returncode == pd.EXIT_NEEDS_ORCH

    events = _trace_lines(run_dir)
    ends = [e for e in events if e["ev"] == "worker-end"]
    assert ends and all(e["ok"] is False for e in ends)
    assert all(e["receipt_path"] is None for e in ends)
    assert all(e["model_ms"] is None and e["wrapper_ms"] is None for e in ends)
    receipts = json.loads((run_dir / "receipts.json").read_text())
    assert len(receipts["calls"]) == len(ends)


# --- criterion 4: the stall drill (S1, falsification) -----------------------


@pytest.mark.skipif(not SLOW.exists() or not BASH_OK, reason="slow fixture unavailable")
def test_stall_drill_emits_worker_stall_and_heartbeat(tmp_path, monkeypatch):
    """Falsifies S1: a call that goes silent past STALL_AFTER_S gets exactly
    the trace event + heartbeat the design promises, and is NOT killed for
    it (a stall is a statement, not a failure)."""
    monkeypatch.setattr(pd, "STALL_AFTER_S", 1)
    d = pd.Driver(_args(run_dir=str(tmp_path), worker_cmd_override=str(SLOW)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    view = d.make_view_dir("stall-test", [])
    monkeypatch.setenv("MOCK_WORKER_SLEEP_S", "3")
    monkeypatch.setenv("MOCK_WORKER_SLEEP_ROLE", "risk")

    res = d.run_worker("risk", "mock-model", "ROLE: risk\nRUN: TEST\nprompt",
                       view, 30, {"stage": "risk"})
    assert res.get("ok") is True, res

    events = _trace_lines(tmp_path)
    stalls = [e for e in events if e["ev"] == "worker-stall"]
    assert stalls and stalls[0]["role"] == "risk"
    assert stalls[0]["age_s"] >= 1
    assert stalls[0]["budget_left_s"] >= 0


# --- criterion 5: the wrapper drill (S2, falsification) ---------------------


@pytest.mark.skipif(not WRAPPER_LAG.exists() or not BASH_OK,
                    reason="wrapper-lag fixture unavailable")
def test_wrapper_drill_emits_wrapper_overhead_anomaly(tmp_path, monkeypatch):
    """Falsifies S2: driver-wall minus receipt durationMs, attributed to the
    WRAPPER not the model -- the exact distinction a misdiagnosed wrapper
    stall gets wrong."""
    monkeypatch.setattr(pd, "WRAPPER_ANOMALY_FLOOR_MS", 500)
    monkeypatch.setattr(pd, "WRAPPER_ANOMALY_FRAC", 0.1)
    d = pd.Driver(_args(run_dir=str(tmp_path), worker_cmd_override=str(WRAPPER_LAG)))
    d.tmp_root = str(tmp_path / "views")
    os.makedirs(d.tmp_root, exist_ok=True)
    view = d.make_view_dir("wrapper-test", [])
    monkeypatch.setenv("MOCK_WORKER_WRAPPER_SLEEP_S", "2")
    monkeypatch.setenv("MOCK_WORKER_WRAPPER_ROLE", "risk")

    res = d.run_worker("risk", "mock-model", "ROLE: risk\nRUN: TEST\nprompt",
                       view, 30, {"stage": "risk"})
    assert res.get("ok") is True, res
    assert res.get("receipt_duration_ms") == 1000

    events = _trace_lines(tmp_path)
    anomalies = [e for e in events if e["ev"] == "anomaly" and e["kind"] == "wrapper-overhead"]
    assert anomalies, [e for e in events if e["ev"] in ("worker-end", "anomaly")]
    assert anomalies[0]["role"] == "risk"
    assert anomalies[0]["ms"] > 0


# --- criterion 6: compare -----------------------------------------------


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_compare_cli_shows_deltas_and_config_diff_and_warns_on_schema_mismatch(tmp_path):
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    assert _run(r1).returncode == 0
    assert _run(r2).returncode == 0

    proc = subprocess.run([PY, str(TRACE_CLI), "compare", str(r1), str(r2)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(r"writer.*[+-][0-9]+ms", proc.stdout), proc.stdout
    assert "CONFIG:" in proc.stdout

    summary2 = r2 / "trace" / "summary.json"
    data = json.loads(summary2.read_text())
    data["schema"] = 0
    summary2.write_text(json.dumps(data))
    proc2 = subprocess.run([PY, str(TRACE_CLI), "compare", str(r1), str(r2)],
                           capture_output=True, text=True)
    assert proc2.returncode == 0
    assert "schema mismatch" in proc2.stderr


# --- criterion 7: operator against the real, verified audited rollout ------


_ROLLOUT_GLOB = list((Path.home() / ".codex" / "sessions").glob(
    "**/rollout-*019f881c*.jsonl")) if (Path.home() / ".codex" / "sessions").exists() else []


@pytest.mark.skipif(not _ROLLOUT_GLOB, reason="audited-run rollout not present on this machine")
@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_operator_pins_to_the_audited_run(tmp_path):
    run_dir = tmp_path / "run"
    assert _run(run_dir).returncode == 0
    proc = subprocess.run(
        [PY, str(TRACE_CLI), "operator", str(run_dir), "--rollout", str(_ROLLOUT_GLOB[0])],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    op = json.loads((run_dir / "trace" / "operator.json").read_text())
    assert op["model_requests"] == 328
    assert op["tokens"]["output"] is not None


def test_operator_missing_rollout_exits_3_and_never_guesses(tmp_path):
    result, err = trace_mod.extract_operator(tmp_path, rollout_path=str(tmp_path / "nope.jsonl"))
    assert result is None
    assert "not found" in err


# --- criterion 8: no side effects on run_stats.py ---------------------------


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_run_stats_output_unaffected_by_trace_presence(tmp_path):
    run_dir = tmp_path / "run"
    assert _run(run_dir).returncode == 0
    assert (run_dir / "trace" / "trace.jsonl").exists()

    with_trace = subprocess.run(
        [PY, str(ROOT / "scripts" / "run_stats.py"), str(run_dir), "--json"],
        capture_output=True, text=True)
    assert with_trace.returncode == 0, with_trace.stderr

    aside = run_dir.parent / "trace-aside"
    shutil.move(str(run_dir / "trace"), str(aside))
    try:
        without_trace = subprocess.run(
            [PY, str(ROOT / "scripts" / "run_stats.py"), str(run_dir), "--json"],
            capture_output=True, text=True)
    finally:
        shutil.move(str(aside), str(run_dir / "trace"))
    assert without_trace.returncode == 0, without_trace.stderr
    assert with_trace.stdout == without_trace.stdout


def test_run_stats_receipt_glob_never_matches_trace_files():
    """Direct proof `RECEIPT_FILE_RE` (run_stats.py, NOT touched by this
    feature) cannot match any filename the profiler writes."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_stats
    for name in ("trace.jsonl", "summary.json", "operator.json"):
        assert not run_stats.RECEIPT_FILE_RE.match(name), name


# --- criterion 9: a killed run's trace is still summarizable ---------------


def _crash_mid_stage2(run_dir):
    args = pd.build_arg_parser().parse_args(
        ["--ticker", "TEST", "--run-dir", str(run_dir), "--routing", str(ROUTING),
         "--worker-cmd-override", str(MOCK)])
    d = pd.Driver(args)
    d.load_routing()
    d.run_dir.mkdir(parents=True, exist_ok=True)
    d.tmp_root = tempfile.mkdtemp(prefix="trdrv-views-crashtest-")
    d.trace.emit("run-start", schema=1, run_id=d.run_id, driver_version=pd.DRIVER_VERSION,
                mode="mock", ticker=d.ticker, routing=d.routing,
                routing_sha256=d.routing_sha256)
    try:
        d.stage1()
        d.stage_start("2", "analysts")   # opened, never closed -- the "kill"
    finally:
        d.cleanup_views()


@pytest.mark.skipif(not MOCK.exists() or not BASH_OK, reason="mock worker unavailable")
def test_summary_recomputes_from_l1_after_a_kill_mid_stage(tmp_path):
    run_dir = tmp_path / "run"
    _crash_mid_stage2(run_dir)
    assert not (run_dir / "DRIVER-STATE.json").exists()      # a real kill
    assert not (run_dir / "trace" / "summary.json").exists()

    proc = subprocess.run([PY, str(TRACE_CLI), "summary", str(run_dir)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "stage 1" in proc.stdout
    assert "run " in proc.stdout
