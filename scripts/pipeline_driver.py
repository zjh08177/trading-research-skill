#!/usr/bin/env python3
"""Deterministic driver for pipeline Stages 1-7c. Stdlib only.

WHY THIS EXISTS. The audited Codex run (019f881c, 82 min, 328 model requests,
ONE ticker) spent 63% of its wall clock on orchestrator reasoning spread over
~200 mechanical turns: spawn a worker, wait, read stdout, write a file, decide
what is next. None of that needs a model. This driver owns every one of those
turns as ordinary Python, so the orchestrator's whole job collapses to: write
`routing.json`, launch this as ONE background cell, poll it with no analysis,
then publish from `DRIVER-STATE.json`.

Every LLM worker is a `cursor-delegate.sh` subprocess (prompt on stdin, never
argv). The driver -- not the worker -- writes every artifact, so workers stay
read-only and the artifact/prompt bytes are under deterministic control.

FAIL LOUD, NEVER DEGRADE. A DAG defect must block the run, not get improvised
around: any condition the driver cannot honestly resolve exits 10 with a
machine-readable `reason` in `DRIVER-STATE.json` for the orchestrator to handle.
The only "keep going" paths are the ones SKILL.md already prescribes (a dead or
MALFORMED role becomes a named Data Gap; a twice-failing QA becomes a
QA-exceptions box).

Exit codes
  0   published-ready   -- artifacts complete, Stage 8 is the orchestrator's
  10  needs-orchestrator-- see DRIVER-STATE.json:reason {code, detail}
  20  abstain           -- P1 unfillable; abstain scope + no_call ledger row emitted
  2   bad invocation    -- out-of-scope flags, unreadable routing, missing inputs

Scope v1 (mirrors the cursor host's R4): single-ticker, live mode only. Batch,
crypto, `--options`, and replay are rejected at parse time with exit 2.

The per-stage view dirs are temp COPIES of run artifacts (the writer's holds
real `15-position.*`), so they are deleted at exit on every path — clean, exit
10, and exit 20 alike. `--keep-views` retains them for debugging, loudly.

Stage 8 is deliberately NOT here: this driver never copies to the vault and
never appends to the canonical ledger. It emits the INTENDED ledger row into
`DRIVER-STATE.json` (and `80-ledger-row.json`) and stops.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import validate_artifact  # noqa: E402  -- the artifact accept-gate (TASK 1)

EXIT_OK = 0
EXIT_NEEDS_ORCH = 10
EXIT_ABSTAIN = 20
EXIT_BAD_INVOCATION = 2

DRIVER_VERSION = "1.0"
STATE_FILE = "DRIVER-STATE.json"
MANIFEST_FILE = "RUN-MANIFEST.md"
RECEIPTS_FILE = "receipts.json"

# --- routing ---------------------------------------------------------------
# Defaults are the cross-vendor model-slot table in SKILL.md ("Model slots
# (Cursor host)"). `routing.json` overrides ONLY the keys it names -- an
# orchestrator that parses "use opus for the writer" writes one key, not a whole
# table, and every unnamed slot keeps the audited default.
DEFAULT_ROUTING = {
    "analyst": "gpt-5.5-medium",
    "bull": "gpt-5.5-medium",
    "bear": "gpt-5.5-medium",
    "risk": "gpt-5.5-medium",
    "judges": ["gpt-5.5-extra-high", "claude-opus-4-8-thinking-max", "glm-5.2-high"],
    "judges_escalation": ["composer-2.5", "grok-4.3"],
    "writer": "claude-opus-4-8-thinking-high",
    "qa": "gpt-5.5-medium",
}
ROUTING_LIST_KEYS = {"judges": 3, "judges_escalation": 2}

# --- timeouts (seconds) ----------------------------------------------------
# Pinned per stage class. A worker that blows its budget is SIGTERMed, retried
# once, then quarantined as a data gap -- never waited on forever, and never
# silently dropped.
TIMEOUTS = {
    "analyst": 240,
    "bull": 300, "bear": 300, "risk": 300,
    "judge": 420,
    "writer": 900,
    "prose_qa": 420, "qa_fix": 420,
}

# The delegate's proof-of-invocation line (cursor-delegate.sh, stderr). A plain
# non-empty stdout does NOT prove a model ran; only a receipt whose promptSha256
# matches the bytes we sent does.
RECEIPT_LINE_RE = re.compile(r"\[[a-z-]*delegate\]\s+receipt:\s*(\S+)")

# Role-card headings in references/prompts.md, matched by prefix so the em-dash
# tails ("(wave 1 - runs first...)") can churn without breaking the lookup.
CARD_HEADINGS = {
    "house": "House rules",
    "fund": "Fundamental analyst",
    "tech": "Technical analyst",
    "sent": "Sentiment / news analyst",
    "meanrev": "Mean-Reversion / Exhaustion analyst",
    "bull": "Bull advocate",
    "bear": "Bear advocate",
    "risk": "Risk officer",
    "judge": "Judge",
    "writer": "Report writer",
    "prose_qa": "QA prose checker",
}

ANALYSTS = ("fund", "tech", "sent", "meanrev")
ANALYST_TITLE = {"fund": "FUNDAMENTAL ANALYST", "tech": "TECHNICAL ANALYST",
                 "sent": "SENTIMENT ANALYST", "meanrev": "MEAN-REVERSION ANALYST"}

POSITION_GLOB = "15-position."

# Where a NON-FINAL tally's block is written. `55-rating-block.md` is the file
# invariant 1 inserts verbatim into the report and the sentinel --resume stats
# for stage 5, so it must only ever hold a tally no further judge round can
# change (see Driver._tally).
PROVISIONAL_TALLY = "55-tally-provisional.md"
# The ensemble decisions that mean "another judge round is coming".
NONFINAL_DECISIONS = ("escalate", "backfill")

# Artifact -> one-line description, for RUN-MANIFEST.md. Anything not listed
# still appears in the manifest, described as "(undescribed artifact)" -- an
# unexplained file is reported, never hidden.
ARTIFACT_DESCRIPTIONS = {
    "00-scope.md": "Stage 0 scope: job class, ticker, asset class, as-of.",
    "10-datapack.json": "Stage 1 data pack, fact-id keyed (P1-P9).",
    "10-datapack.md": "Stage 1 data pack rendered; injected verbatim into every worker prompt.",
    "11-history.json": "Stage 1c raw price history (tiingo) behind the P9 facts.",
    "15-position.json": "Stage 1b live position facts (H1.*). Withheld from every stage but the writer (invariant 12).",
    "15-position.md": "Stage 1b position rendered. Same withholding rule.",
    "20-analyst-fund.md": "Stage 2 fundamental analyst brief.",
    "20-analyst-tech.md": "Stage 2 technical analyst brief.",
    "20-analyst-sent.md": "Stage 2 sentiment/news analyst brief.",
    "20-analyst-meanrev.md": "Stage 2 mean-reversion/exhaustion analyst brief.",
    "30-debate.md": "Stage 3 debate: bull case (wave 1) then bear case (wave 2, reads the bull).",
    "40-riskbox-block.md": "Stage 4a computed risk box (risk_box.py); inserted VERBATIM (invariant 16).",
    "40-risk.md": "Stage 4b risk officer: the verbatim block, then narration.",
    "45-judge-bundle.md": "Stage 5 immutable judge input, assembled ONCE and sent byte-identically to every judge.",
    "53-meanrev-block.md": "Stage 6b computed mean-reversion block (render_meanrev.py); inserted VERBATIM.",
    "55-rating-block.md": "Stage 5b ensemble rating block (ensemble.py); inserted VERBATIM (invariant 1). Written ONLY by a final tally.",
    "55-tally-provisional.md": "Stage 5b PROVISIONAL tally — the n=3 round that escalated. Superseded by 55-rating-block.md; never published.",
    "55-decision.json": "Stage 5b ensemble decision JSON (publish/escalate/backfill/no-call).",
    "56-levels.json": "Decision levels parsed from the report's LEVELS_JSON (render_report.py).",
    "60-report.md": "Stage 6 report — the CANONICAL artifact.",
    "60-report.html": "Stage 7b rendered report (render_report.py); deterministic, never hand-authored.",
    "70-qa-prose.txt": "Stage 7 prose-QA response, persisted VERBATIM.",
    "70-qa-prefooter.txt": "Stage 7 qa_check.py output (pre-footer pass, no --check-footer).",
    "70-qa-final.txt": "Stage 7c qa_check.py output after the footer patch (with --check-footer).",
    "71-run-stats.json": "Stage 7c receipt-backed disclosure stats (run_stats.py --json).",
    "80-ledger-row.json": "INTENDED Stage 8 ledger row. NOT appended — that is the orchestrator's gated step.",
    RECEIPTS_FILE: "THE receipt census — every worker call: stage, model, delegate "
                   "receipt path, exit code, acceptance. Rewritten after every stage; "
                   "run_stats.py reads it to compute the disclosure footer.",
    MANIFEST_FILE: "This file.",
    STATE_FILE: "Machine-readable driver state: per-stage status/timings, quarantines, view-dir audit, exit reason.",
}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DriverError(Exception):
    """A condition the driver cannot honestly resolve. Carries the
    machine-readable reason the orchestrator acts on."""

    def __init__(self, code, detail, exit_code=EXIT_NEEDS_ORCH):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.exit_code = exit_code


class WorkerResult(dict):
    """Plain dict; attribute-free on purpose so it serializes into receipts.json
    without a conversion step."""


# ---------------------------------------------------------------------------


class Driver:
    def __init__(self, args):
        self.args = args
        self.ticker = args.ticker.upper()
        self.run_dir = Path(args.run_dir).resolve()
        self.skill_dir = Path(args.skill_dir).resolve()
        self.python = str(self.skill_dir / ".venv" / "bin" / "python")
        if not os.path.exists(self.python):
            self.python = sys.executable
        self.asof = args.asof
        self.stamp = args.stamp
        self.run_id = f"{self.ticker}-{self.asof}-{self.stamp}"
        self.mock = bool(args.worker_cmd_override) or args.mock
        self.worker_cmd = shlex.split(args.worker_cmd_override) if args.worker_cmd_override \
            else [os.path.expanduser(args.worker_wrapper)]
        self.routing = {}
        self.started = time.time()
        self.started_iso = now_iso()
        self.stages = []
        self.call_records = []
        self.rounds = []
        self.quarantines = []
        self.data_gaps = []
        self.view_dirs = []
        self.position_view_violations = []
        self.tmp_root = None
        self.views_removed = None
        self.ensemble = {}
        self.qa = {}
        self.notes = []
        self._cards = None
        self._stage_open = None
        self._receipt_lock = threading.Lock()

    # --- logging ----------------------------------------------------------

    def hb(self, stage, status, extra=""):
        """One stdout line per state change. The orchestrator's poll reads these
        and does NO analysis on them."""
        line = f"[driver] {now_iso()} stage={stage} status={status}"
        if extra:
            line += f" {extra}"
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def warn(self, msg):
        sys.stderr.write(f"[driver] WARNING {msg}\n")
        sys.stderr.flush()
        self.notes.append(msg)

    def gap(self, msg):
        """Record a Data Gap. Every gap reaches BOTH the report's Data gaps
        section and DRIVER-STATE.json -- a gap the orchestrator cannot see is
        the silent-degradation failure this driver exists to prevent."""
        if msg not in self.data_gaps:
            self.data_gaps.append(msg)
        self.hb("-", "data-gap", msg[:120])

    # --- stage bookkeeping -------------------------------------------------

    def stage_start(self, stage, name):
        self._stage_open = {"stage": stage, "name": name, "status": "running",
                            "started_at": now_iso(), "started_s": time.time(),
                            "notes": []}
        self.stages.append(self._stage_open)
        self.hb(stage, f"{name}-start")
        return self._stage_open

    def stage_end(self, status="ok", note=None):
        st = self._stage_open
        if st is None:
            return
        st["status"] = status
        st["ended_at"] = now_iso()
        st["elapsed_s"] = round(time.time() - st.pop("started_s"), 1)
        if note:
            st["notes"].append(note)
        self.hb(st["stage"], f"{st['name']}-{status}", f"{st['elapsed_s']}s")
        self._stage_open = None

    def stage_note(self, note):
        if self._stage_open is not None:
            self._stage_open["notes"].append(note)

    def stage_skip(self, stage, name, why):
        self.stages.append({"stage": stage, "name": name, "status": "skipped",
                            "started_at": now_iso(), "ended_at": now_iso(),
                            "elapsed_s": 0.0, "notes": [why]})
        self.hb(stage, f"{name}-skipped", why)

    # --- paths -------------------------------------------------------------

    def p(self, *parts):
        return self.run_dir.joinpath(*parts)

    def exists(self, *parts):
        return self.p(*parts).exists()

    def read(self, *parts):
        return self.p(*parts).read_text(encoding="utf-8")

    def write(self, rel, text):
        path = self.p(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # --- routing -----------------------------------------------------------

    def load_routing(self):
        """DEFAULT_ROUTING overlaid with the orchestrator's routing.json. An
        unknown key or a wrong-length judge list is a BAD INVOCATION, not a
        silently-ignored typo -- a run that quietly used the default panel when
        the user asked for a different one is an undisclosed routing change."""
        path = Path(self.args.routing)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DriverError("routing-unreadable", f"{path}: {exc}", EXIT_BAD_INVOCATION)
        except ValueError as exc:
            raise DriverError("routing-malformed", f"{path}: {exc}", EXIT_BAD_INVOCATION)
        if not isinstance(raw, dict):
            raise DriverError("routing-malformed", f"{path}: top level is not an object",
                              EXIT_BAD_INVOCATION)
        unknown = sorted(set(raw) - set(DEFAULT_ROUTING))
        if unknown:
            raise DriverError(
                "routing-unknown-key",
                f"{path}: unknown slot(s) {unknown}; known slots: "
                f"{sorted(DEFAULT_ROUTING)}", EXIT_BAD_INVOCATION)
        routing = {k: (list(v) if isinstance(v, list) else v)
                   for k, v in DEFAULT_ROUTING.items()}
        for key, value in raw.items():
            want = ROUTING_LIST_KEYS.get(key)
            if want is not None:
                if not isinstance(value, list) or len(value) != want or \
                        not all(isinstance(x, str) and x.strip() for x in value):
                    raise DriverError(
                        "routing-bad-value",
                        f"{path}: '{key}' must be a list of exactly {want} non-empty "
                        f"model slugs, got {value!r}", EXIT_BAD_INVOCATION)
            elif not isinstance(value, str) or not value.strip():
                raise DriverError("routing-bad-value",
                                  f"{path}: '{key}' must be a non-empty model slug, "
                                  f"got {value!r}", EXIT_BAD_INVOCATION)
            routing[key] = value
        self.routing = routing
        self.hb("0", "routing-loaded", json.dumps(routing, sort_keys=True))

    # --- role cards --------------------------------------------------------

    def cards(self):
        """Parse references/prompts.md into {key: fenced card body}. A missing
        card is fatal: inventing a role card would change what the pipeline
        actually asked the model to do."""
        if self._cards is not None:
            return self._cards
        path = self.skill_dir / "references" / "prompts.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DriverError("role-cards-unreadable", f"{path}: {exc}")
        blocks = {}
        for m in re.finditer(r"^##\s+(.+?)\s*$\n+```\n(.*?)^```\s*$",
                             text, re.M | re.S):
            blocks[m.group(1).strip()] = m.group(2)
        cards, missing = {}, []
        for key, prefix in CARD_HEADINGS.items():
            hit = next((h for h in blocks if h.lower().startswith(prefix.lower())), None)
            if hit is None:
                missing.append(prefix)
            else:
                cards[key] = blocks[hit].rstrip()
        if missing:
            raise DriverError("role-card-missing",
                              f"{path}: no fenced card under heading(s) {missing}")
        self._cards = cards
        return cards

    # --- view dirs ---------------------------------------------------------

    def make_view_dir(self, label, rel_files, allow_position=False):
        """A stage-scoped, read-only workspace holding ONLY that stage's
        read-set (SKILL.md Pipeline table).

        This is what makes invariant 12 MECHANICAL instead of prompt-enforced:
        no view except the writer's may contain `15-position.*`, and the driver
        asserts it here rather than trusting a role card to say so. A violation
        is recorded and raised, never tolerated."""
        base = Path(tempfile.mkdtemp(prefix=f"trdrv-{label}-", dir=self.tmp_root))
        copied = []
        for rel in rel_files:
            src = self.p(rel) if not os.path.isabs(str(rel)) else Path(rel)
            if not src.exists():
                continue
            dst = base / Path(rel).name
            shutil.copy2(src, dst)
            copied.append(dst.name)
        leaked = [n for n in copied if n.startswith(POSITION_GLOB)]
        record = {"label": label, "path": str(base), "files": sorted(copied),
                  "contains_position": bool(leaked),
                  "position_allowed": bool(allow_position)}
        self.view_dirs.append(record)
        if leaked and not allow_position:
            self.position_view_violations.append(record)
            raise DriverError(
                "invariant-12-violation",
                f"view dir for '{label}' contains position artifact(s) {leaked}; "
                "only the writer's view may (SKILL.md invariant 12)")
        return str(base)

    def cleanup_views(self):
        """Delete the whole view-dir tree. Idempotent, and it runs on the
        exit-10/exit-20 paths too.

        A view dir is a COPY of run artifacts in system temp, outside the run
        folder's retention and .gitignore control — and the writer's view
        legitimately holds `15-position.*`, i.e. real account holdings and P&L.
        Leaving that behind on every run is an unbounded, ungoverned spill of
        position data, so the copies die with the run that made them. Failure to
        remove them is REPORTED, never swallowed; `--keep-views` opts out loudly
        for debugging."""
        if self.views_removed is not None:          # already decided; say it once
            return
        if not self.tmp_root or not os.path.exists(self.tmp_root):
            self.views_removed = True
            return
        if getattr(self.args, "keep_views", False):
            self.views_removed = False
            self.warn(f"--keep-views: stage view dirs KEPT at {self.tmp_root}. They "
                      "include the writer's copy of 15-position.* (real holdings); "
                      "delete them yourself when done.")
            return
        try:
            shutil.rmtree(self.tmp_root)
            self.views_removed = True
        except OSError as exc:                                        # noqa: BLE001
            self.views_removed = False
            self.warn(f"could NOT remove the view-dir tree {self.tmp_root}: {exc}; "
                      "copies of this run's artifacts (including 15-position.* in the "
                      "writer's view) are still on disk — remove them by hand.")

    # --- worker ------------------------------------------------------------

    def record_call(self, res):
        """Append one normalized call to the run's SINGLE receipt census and
        rewrite `receipts.json`.

        One census, not per-stage files: `run_stats.py` globs every
        `*receipt*.json` in the run folder and unions them, so an aggregate
        sitting alongside per-stage copies of the same calls would double every
        agent count and model tally in the disclosure footer — precisely the
        invariant-7 defect this rewrite exists to close. The census is rewritten
        after every call so Stage 7c (which runs BEFORE finish()) sees a complete
        one. Per-call provenance also stays on each artifact's `RECEIPT:` header."""
        record = self._call_record(res)
        res["_record"] = record        # so the accept gate can amend it in place
        with self._receipt_lock:
            self.call_records.append(record)
        self.flush_receipts()
        return record

    def flush_receipts(self):
        with self._receipt_lock:
            # Sorted, not append-ordered: parallel analysts and judges finish in
            # a different order every run, and run_stats.py builds the footer's
            # model mix in encounter order — so an unsorted census makes two
            # identical runs disclose the same models in a different sequence.
            calls = sorted(self.call_records, key=lambda c: (
                c.get("startedAtMs") or 0, str(c.get("stage")),
                c.get("slot") or 0, str(c.get("role")), c.get("attempt") or 0))
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.p(RECEIPTS_FILE).write_text(json.dumps(
                {"run_id": self.run_id, "driver_version": DRIVER_VERSION,
                 "mode": "mock" if self.mock else "live",
                 "calls": calls}, indent=2) + "\n", encoding="utf-8")

    def _run_once(self, role, model, prompt, view_dir, timeout_s, attempt, meta):
        """One delegate subprocess. Prompt on STDIN, never argv.

        `cursor-delegate.sh` reads stdin via `$(cat)`, which strips trailing
        newlines before hashing, so the caller must hash the same bytes -- the
        prompt is rstripped of newlines before both sending and hashing, or
        every receipt check would false-fail."""
        cmd = list(self.worker_cmd) + ["--dir", view_dir, "--mode", "read-only",
                                       "--model", model]
        started_ms = int(time.time() * 1000)
        res = WorkerResult({
            "role": role, "model_requested": model, "attempt": attempt,
            "cmd": cmd[0], "view_dir": view_dir, "started_ms": started_ms,
            "promptSha256": sha256(prompt), "prompt_chars": len(prompt),
        })
        res.update(meta or {})
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
        except OSError as exc:
            res.update(ok=False, failure="spawn-failed", detail=str(exc))
            return res
        try:
            out, err = proc.communicate(prompt, timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                out, err = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
            timed_out = True
        res["duration_ms"] = int(time.time() * 1000) - started_ms
        res["exit_code"] = proc.returncode
        res["stderr_tail"] = (err or "")[-800:]
        if timed_out:
            res.update(ok=False, failure="timeout",
                       detail=f"exceeded {timeout_s}s; SIGTERM sent")
            return res
        m = RECEIPT_LINE_RE.search(err or "")
        if not m:
            # No receipt => the delegate never really invoked a model. A
            # plausible-looking stdout is NOT evidence (see the delegate's own
            # header comment); treat it as a call that did not happen.
            res.update(ok=False, failure="no-receipt",
                       detail="no '[...delegate] receipt: <path>' line on stderr — "
                              "the call cannot be proven to have reached a model")
            return res
        receipt_path = m.group(1)
        res["receipt_path"] = receipt_path
        try:
            blob = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            res.update(ok=False, failure="receipt-unreadable",
                       detail=f"{receipt_path}: {exc}")
            return res
        res["cli"] = blob.get("cli")
        res["cliModel"] = blob.get("cliModel")
        res["receipt_exit_code"] = blob.get("exitCode")
        if blob.get("promptSha256") != res["promptSha256"]:
            res.update(ok=False, failure="receipt-prompt-mismatch",
                       detail=f"receipt promptSha256={blob.get('promptSha256')!r} != "
                              f"sent {res['promptSha256']!r}")
            return res
        if proc.returncode != 0:
            res.update(ok=False, failure="nonzero-exit",
                       detail=f"delegate exited {proc.returncode}")
            return res
        if not (out or "").strip():
            res.update(ok=False, failure="empty-output", detail="delegate produced no stdout")
            return res
        res.update(ok=True, text=out, outputSha256=sha256(out))
        return res

    def run_worker(self, role, model, prompt, view_dir, timeout_s, meta=None):
        """One delegate call with ONE infrastructure retry (timeout, no receipt,
        nonzero exit, empty output). Returns the last WorkerResult either way."""
        prompt = prompt.rstrip("\n")
        for attempt in (1, 2):
            res = self._run_once(role, model, prompt, view_dir, timeout_s, attempt, meta)
            self.record_call(res)
            if res.get("ok"):
                return res
            self.warn(f"{role}: attempt {attempt} failed "
                      f"({res['failure']}: {res['detail']})")
            if attempt == 1:
                self.hb("-", f"{role}-retry", res["failure"])
        return res

    def worker_artifact(self, role, model, prompt, view_dir, timeout_s,
                        accept_role=None, quarantine_rel=None, label=None,
                        meta=None):
        """Full worker contract: call -> receipt-verify -> accept-gate.

        `accept_role` is the validate_artifact role. A MALFORMED artifact gets
        exactly ONE respawn (a fresh call, not a re-parse); a second failure is
        quarantined to `quarantine_rel` and becomes a named Data Gap -- the same
        posture SKILL.md's failure map gives a dead role. Nothing downstream
        ever reads an artifact that did not pass this gate.

        Returns (text|None, last_result)."""
        label = label or role
        last = None
        for spawn in (1, 2):
            res = self.run_worker(role, model, prompt, view_dir, timeout_s, meta)
            last = res
            if not res.get("ok"):
                if spawn == 1:
                    self.hb("-", f"{label}-respawn", f"infrastructure: {res['failure']}")
                    continue
                break
            text = res["text"]
            if accept_role is None:
                if text.strip():
                    return text, res
                reasons = ["MALFORMED(truncation): worker returned only whitespace"]
            else:
                ok, reasons = validate_artifact.check(accept_role, text)
                if ok:
                    return text, res
            res["accept_reasons"] = reasons
            # The census entry was written when the call returned; correct it now
            # that the accept gate has ruled, so receipts.json never records a
            # rejected artifact as accepted.
            rec = res.get("_record")
            if rec is not None:
                rec["accepted"] = False
                rec["rejectReasons"] = reasons
                self.flush_receipts()
            self.warn(f"{label}: artifact rejected by validate_artifact — "
                      + " | ".join(reasons))
            if spawn == 1:
                self.hb("-", f"{label}-respawn", "MALFORMED")
                continue
            if quarantine_rel:
                self.write(quarantine_rel, text)
        # Both spawns failed. Quarantine what we have and name the gap.
        reasons = (last or {}).get("accept_reasons") or \
            [f"{(last or {}).get('failure', 'unknown')}: {(last or {}).get('detail', '')}"]
        if quarantine_rel and last and last.get("text") and not self.exists(quarantine_rel):
            self.write(quarantine_rel, last["text"])
        self.quarantines.append({
            "role": label, "model": model, "attempts": 2,
            "quarantined_to": quarantine_rel, "reasons": reasons,
            "receipt_path": (last or {}).get("receipt_path"),
        })
        self.gap(f"MISSING({label}): worker output rejected twice — "
                 + ("raw preserved at " + quarantine_rel + "; " if quarantine_rel else "")
                 + "; ".join(reasons)[:400])
        return None, last

    # --- subprocess helpers -------------------------------------------------

    def run_script(self, rel_script, script_args, expect=(0,), stdout_to=None,
                   env=None):
        """Run one of the skill's own deterministic scripts. A non-expected exit
        is a hard stop with the script's stderr attached -- never swallowed."""
        cmd = [self.python, str(self.skill_dir / rel_script)] + [str(a) for a in script_args]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env={**os.environ, **(env or {})})
        if proc.returncode not in expect:
            raise DriverError(
                f"script-failed:{Path(rel_script).name}",
                f"exit {proc.returncode} for `{' '.join(cmd)}`: "
                f"{(proc.stderr or proc.stdout).strip()[:600]}")
        if stdout_to is not None:
            self.write(stdout_to, proc.stdout)
        return proc

    # --- prompts -----------------------------------------------------------

    def build_prompt(self, role, card_key, context_md, slots):
        """House rules + role card + a driver-owned OUTPUT CONTRACT.

        The OUTPUT CONTRACT is additive and load-bearing: validate_artifact
        enforces shapes (the `## Bull case` heading, the single `KEY POINTS:`
        terminator, the 4-field VERDICT line) that the role cards in
        prompts.md do not all request. Without it the accept-gate would
        quarantine every well-reasoned debate artifact for a missing heading.
        The first line is `ROLE:` so a worker (and the mock) can identify the
        slot without parsing the whole prompt."""
        cards = self.cards()
        house = cards["house"].replace("{{datapack_md}}", context_md)
        body = cards[card_key]
        for key, value in slots.items():
            body = body.replace("{{" + key + "}}", value)
        contract = OUTPUT_CONTRACTS.get(role, "")
        parts = [
            f"ROLE: {role}",
            f"RUN: {self.run_id}",
            "You are running HEADLESS in a read-only workspace. Your ENTIRE stdout "
            "becomes the artifact — emit the artifact text only, with no preamble, "
            "no sign-off, and no commentary about the task. Do not write, edit, or "
            "create any file; the driver persists your output. Use only the "
            "material in this prompt and the files in your workspace.",
            "",
            house,
            "",
            body,
        ]
        if contract:
            parts += ["", "OUTPUT CONTRACT (enforced mechanically by "
                          "scripts/validate_artifact.py — an artifact that breaks it "
                          "is quarantined, not published):", contract]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # STAGE 1 — data pack + position
    # ------------------------------------------------------------------

    def stage1(self):
        self.stage_start("1", "datapack")
        if self.mock:
            self._stage1_mock()
        else:
            self._stage1_live()
        pack_path = self.p("10-datapack.json")
        if not pack_path.exists():
            raise DriverError("stage1-no-pack", f"{pack_path} was not produced")
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        # Invariant 5 / failure map: dead P1 => abstain report + no_call row.
        if not any(k.startswith("P1.") for k in pack) or (
                pack.get("P1.last") is None and pack.get("P1.price") is None):
            self.stage_end("failed", "P1 unfillable")
            raise DriverError("p1-unfillable",
                              "the pack carries no P1 price fact; SKILL.md failure map "
                              "requires an abstain report + a no_call ledger row",
                              EXIT_ABSTAIN)
        self.stage_note(f"{len(pack)} facts")
        if not self.exists("15-position.json"):
            self.gap("MISSING(position): no 15-position.json — the run is "
                     "position-blind; 'Your position' is omitted.")
        self.stage_end("ok")

    def _stage1_mock(self):
        """MOCK MODE. Seeds the pack from a real audited-run fixture instead of
        calling live vendors. Announced loudly on stdout and stamped into
        DRIVER-STATE.json — a mock run must never be mistakable for a live one."""
        # Default to the COMMITTED fixture, not a gitignored `runs/` dir — mock
        # mode has to work on a fresh clone, not just on the machine that
        # happens to still hold the audited run.
        fixture = Path(self.args.datapack_fixture or
                       (self.skill_dir / "tests" / "fixtures" / "driver" / "pack"
                        / "10-datapack.json"))
        if not fixture.exists():
            raise DriverError("mock-fixture-missing", str(fixture), EXIT_BAD_INVOCATION)
        self.hb("1", "MOCK-datapack-fixture", str(fixture))
        src_dir = fixture.parent
        self.run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixture, self.p("10-datapack.json"))
        for name in ("10-datapack.md", "11-history.json",
                     "15-position.json", "15-position.md"):
            if (src_dir / name).exists():
                shutil.copy2(src_dir / name, self.p(name))
        if not self.exists("00-scope.md"):
            self.write("00-scope.md",
                       f"# Scope\n- Query: MOCK driver run ({self.ticker}).\n"
                       f"- Job class: J1 single-name deep dive.\n"
                       f"- Ticker: {self.ticker} · kind: equity · As-of: {self.asof}\n"
                       f"- MOCK MODE: data pack seeded from {fixture}; no vendor was called.\n")
        self.stage_note(f"MOCK: pack seeded from {fixture}")

    def _stage1_live(self):
        """One subprocess to the batch datapack builder, which covers the whole
        interactive Stage-1 contract (10-datapack.*, 11-history.json, P9 facts,
        live P1.last via uw_quote, 15-position.*) EXCEPT one thing: it reads the
        position from a pre-fetched holdings snapshot rather than fetching it,
        and exits 4 without one. So the driver resolves the snapshot first, per
        SKILL.md's portfolio runbook (snapshot_holdings.py is the day's SSOT).

        When no snapshot can be obtained, the driver does NOT let the builder
        render a fabricated flat book: it deletes the resulting 15-position.*
        and names the gap. "Book unavailable" and "book empty" are different
        facts and must never collapse into each other."""
        holdings, gap_reason = self._resolve_holdings()
        summary = self.run_script(
            "scripts/batch/build_datapack.py",
            [json.dumps([[self.ticker, "equity"]]), "--asof", self.asof,
             "--stamp", self.stamp, "--profile", "full", "--holdings", holdings])
        try:
            rows = json.loads(summary.stdout)
        except ValueError as exc:
            raise DriverError("stage1-unparseable-summary",
                              f"build_datapack.py stdout was not JSON: {exc}")
        row = rows[0] if rows else {}
        built = Path(row.get("run_dir", "")) if row.get("run_dir") else None
        if built and built.resolve() != self.run_dir:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            for name in ("00-scope.md", "10-datapack.json", "10-datapack.md",
                         "11-history.json", "15-position.json", "15-position.md"):
                if (built / name).exists():
                    shutil.copy2(built / name, self.p(name))
            self.stage_note(f"artifacts copied from {built}")
        if gap_reason:
            for name in ("15-position.json", "15-position.md"):
                if self.exists(name):
                    self.p(name).unlink()
            self.gap(f"MISSING(position): {gap_reason} — a book we could not read is "
                     "NOT an empty book, so the flat placeholder was discarded and "
                     "the run continues position-blind.")

    def _resolve_holdings(self):
        """(holdings_path, gap_reason). Explicit --holdings wins; otherwise take
        the day's snapshot SSOT, fetching it if it is not there yet."""
        if self.args.holdings:
            return self.args.holdings, None
        hist = Path(self.args.holdings_dir or (self.skill_dir / "runs" / ".holdings-history"))
        hist.mkdir(parents=True, exist_ok=True)
        snap = hist / f"{self.asof}.json"
        if not snap.exists():
            proc = subprocess.run(
                [self.python, str(self.skill_dir / "scripts/batch/snapshot_holdings.py"),
                 str(hist), self.asof], capture_output=True, text=True)
            if proc.returncode != 0 or not snap.exists():
                empty = hist / f"{self.asof}-unavailable.json"
                empty.write_text(json.dumps({"holdings": []}), encoding="utf-8")
                return str(empty), (f"snapshot_holdings.py exit {proc.returncode}: "
                                    f"{(proc.stderr or proc.stdout).strip()[:200]}")
        return str(snap), None

    # ------------------------------------------------------------------
    # STAGE 2 — analysts x4, parallel
    # ------------------------------------------------------------------

    def stage2(self):
        self.stage_start("2", "analysts")
        pack_md = self._pack_md()
        jobs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for role in ANALYSTS:
                view = self.make_view_dir(f"2-{role}", ["10-datapack.md", "10-datapack.json"])
                prompt = self.build_prompt(role, role, pack_md, {})
                jobs[role] = pool.submit(
                    self.worker_artifact, role, self.routing["analyst"], prompt,
                    view, TIMEOUTS["analyst"], role,
                    f"20-analyst-{role}-malformed.md", f"analyst-{role}",
                    {"stage": "analysts"})
        produced = 0
        for role in ANALYSTS:
            text, res = jobs[role].result()
            if text is not None:
                self.write(f"20-analyst-{role}.md",
                           self._headers(res, role=f"analyst-{role}") + text.strip() + "\n")
                produced += 1
            self.hb("2", "analysts-progress", f"{produced}/{len(ANALYSTS)} accepted")
        if produced == 0:
            self.stage_end("failed", "no analyst brief survived the accept gate")
            raise DriverError("stage2-empty",
                              "all four analyst briefs failed the accept gate; the judge "
                              "bundle would carry no analysis at all")
        self.stage_end("ok", f"{produced}/4 accepted")

    # ------------------------------------------------------------------
    # STAGE 3 — debate, two waves
    # ------------------------------------------------------------------

    def stage3(self):
        self.stage_start("3", "debate")
        pack_md = self._pack_md()
        briefs = self._analyst_briefs_md()

        view = self.make_view_dir("3a-bull", ["10-datapack.md"] + self._analyst_files())
        bull_prompt = self.build_prompt("bull", "bull", pack_md,
                                        {"analyst_briefs": briefs})
        bull, bull_res = self.worker_artifact(
            "bull", self.routing["bull"], bull_prompt, view, TIMEOUTS["bull"],
            "bull", "30-debate-bull-malformed.md", "bull", {"stage": "bull"})
        if bull is None:
            self.write("30-debate.md",
                       "## Bull case\n\nMISSING(bull): the bull advocate's output was "
                       "rejected twice by the accept gate. Raw preserved at "
                       "30-debate-bull-malformed.md. Do not infer that the bear won by "
                       "concession.\n")
        else:
            self.write("30-debate.md",
                       self._headers(bull_res, role="bull") + bull.strip() + "\n")
        self.hb("3", "debate-progress", "bull done, bear starting")

        # Invariant 2: the bear runs AFTER the bull and reads the bull's ACTUAL
        # text -- the file it gets IS 30-debate.md as just written.
        view = self.make_view_dir("3b-bear",
                                  ["10-datapack.md", "30-debate.md"] + self._analyst_files())
        bear_prompt = self.build_prompt("bear", "bear", pack_md, {
            "analyst_briefs": briefs,
            "bull_case": self.read("30-debate.md")})
        bear, bear_res = self.worker_artifact(
            "bear", self.routing["bear"], bear_prompt, view, TIMEOUTS["bear"],
            "bear", "30-debate-bear-malformed.md", "bear", {"stage": "bear"})
        debate = self.read("30-debate.md").rstrip("\n")
        if bear is None:
            debate += ("\n\n## Bear case\n\nMISSING(bear): the bear advocate's output was "
                       "rejected twice by the accept gate. Raw preserved at "
                       "30-debate-bear-malformed.md. Do not infer that the bull won by "
                       "concession.\n")
        else:
            debate += (f"\n\n<!-- bear — MODEL: {bear_res.get('cliModel')} · "
                       f"RECEIPT: {bear_res.get('receipt_path')} -->\n\n"
                       + bear.strip() + "\n")
        self.write("30-debate.md", debate)
        self.stage_end("ok", f"bull={'ok' if bull else 'MISSING'} "
                             f"bear={'ok' if bear else 'MISSING'}")

    # ------------------------------------------------------------------
    # STAGE 4 — computed risk box, then the risk officer
    # ------------------------------------------------------------------

    def stage4(self):
        self.stage_start("4", "risk")
        self.run_script("scripts/risk_box.py", [self.p("10-datapack.json")],
                        stdout_to="40-riskbox-block.md")
        block = self.read("40-riskbox-block.md")
        view = self.make_view_dir("4b-risk", ["10-datapack.md", "30-debate.md",
                                              "40-riskbox-block.md"])
        prompt = self.build_prompt("risk", "risk", self._pack_md(), {
            "debate_md": self.read("30-debate.md"), "riskbox_block": block})
        text, res = self.worker_artifact(
            "risk", self.routing["risk"], prompt, view, TIMEOUTS["risk"],
            "risk", "40-risk-malformed.md", "risk", {"stage": "risk"})
        if text is None:
            # 40-risk.md still has to exist and still has to LEAD with the
            # computed block: the judges' risk section is invariant-16 material,
            # and a missing narration is a named gap, not a missing box.
            self.write("40-risk.md", block.rstrip("\n") +
                       "\n\nMISSING(risk-officer): the risk officer's narration was "
                       "rejected twice by the accept gate; the computed block above is "
                       "unaffected (invariant 16) and stands alone.\n")
        else:
            # No provenance headers here, deliberately: validate_artifact and the
            # judge bundle both require the verbatim block to be the FIRST bytes.
            self.write("40-risk.md", text.strip() + "\n")
        self.stage_end("ok", "narration MISSING" if text is None else "ok")

    # ------------------------------------------------------------------
    # STAGE 5 — judge bundle, panel, tally
    # ------------------------------------------------------------------

    def stage5(self):
        self.stage_start("5", "ensemble")
        bundle = self._assemble_bundle()
        self.write("45-judge-bundle.md", bundle)
        bundle_sha = sha256(bundle)
        self.p("50-votes").mkdir(parents=True, exist_ok=True)

        n_valid, decision = self._judge_round(
            slots=[1, 2, 3], models=self.routing["judges"], bundle=bundle,
            bundle_sha=bundle_sha, n_target=3)
        if decision["decision"] in ("escalate", "backfill"):
            self.hb("5", f"ensemble-{decision['decision']}",
                    f"spread={decision['spread']} n_valid={decision['n_valid']}")
            self._judge_round(
                slots=[4, 5], models=self.routing["judges_escalation"], bundle=bundle,
                bundle_sha=bundle_sha, n_target=5)
            decision = self._tally(5)
        self.ensemble = decision
        if decision["decision"] == "no-call":
            self.gap(f"NO-CALL: ensemble decision '{decision['decision']}' "
                     f"(n_valid={decision['n_valid']}, spread={decision['spread']}); "
                     "the distribution is published under a NO-CALL headline "
                     "(invariants 8/9).")
        self.stage_end("ok", f"decision={decision['decision']} n_valid={decision['n_valid']}")

    def _judge_round(self, slots, models, bundle, bundle_sha, n_target):
        """Judges run in parallel on BYTE-IDENTICAL prompts (invariant 2): one
        prompt string is built once and every slot receives the same bytes, so
        every receipt carries the same promptSha256."""
        prompt = self.build_prompt("judge", "judge", bundle,
                                   {"judge_bundle": "(the bundle reproduced above)"})
        prompt_sha = sha256(prompt.rstrip("\n"))
        jobs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(slots)) as pool:
            for i, slot in enumerate(slots):
                view = self.make_view_dir(f"5-judge{slot}", ["45-judge-bundle.md"])
                jobs[slot] = pool.submit(
                    self.worker_artifact, "judge", models[i], prompt, view,
                    TIMEOUTS["judge"], "vote", f"50-votes/vote-{slot}-malformed.md",
                    f"judge-{slot}", {"stage": f"judges-n{n_target}", "slot": slot})
        done = 0
        for i, slot in enumerate(slots):
            text, res = jobs[slot].result()
            if text is not None:
                # BACKEND/MODEL/SLOT only: ensemble.py's header parser stops at the
                # first line that is not one of those three, so a ROLE:/RECEIPT:
                # line here would land in the vote body and break the tally.
                header = (f"BACKEND: cursor\nMODEL: {res.get('cliModel') or models[i]}\n"
                          f"SLOT: {slot}\n\n")
                self.write(f"50-votes/vote-{slot}.md", header + text.strip() + "\n")
                done += 1
            self.hb("5", "judges-running", f"{done}/{len(slots)}")
        # Invariant 2 evidence: ONE prompt hash for the whole panel proves every
        # judge got byte-identical input. Kept in DRIVER-STATE.json rather than a
        # sidecar receipt file, which run_stats.py would double-count.
        self.rounds.append({"round": f"judges-n{n_target}", "slots": list(slots),
                            "models": list(models), "promptSha256": prompt_sha,
                            "bundleSha256": bundle_sha, "accepted": done})
        decision = self._tally(n_target)
        return decision["n_valid"], decision

    def _tally(self, n_target):
        """Tally the votes cast SO FAR into a block, and say what it decided.

        The block lands on the PROVISIONAL path first and is promoted to
        `55-rating-block.md` only when this tally is FINAL — when no further
        judge round can change it. ensemble.py renders an escalate/backfill
        round's headline as literally "provisional, escalating to N=5", and
        `55-rating-block.md` is both the file invariant 1 inserts VERBATIM into
        the report AND stage 5's `--resume` sentinel. Writing a provisional
        headline there is what let a driver that died between the n=3 tally and
        the n=5 round resume straight past stage 5 and publish 3 judges as 5."""
        proc = self.run_script("scripts/ensemble.py",
                               ["tally", self.p("50-votes"), "--n-target", n_target],
                               stdout_to=PROVISIONAL_TALLY)
        try:
            decision = json.loads(proc.stderr.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise DriverError("ensemble-undecodable",
                              f"ensemble.py emitted no JSON decision line: {exc}; "
                              f"stderr={proc.stderr[-400:]!r}")
        self.write("55-decision.json", json.dumps(decision, indent=2) + "\n")
        if self._tally_is_final(decision, n_target):
            # os.replace, not a copy: the provisional file BECOMES the rating
            # block, so the two can never disagree and no stale round survives.
            os.replace(self.p(PROVISIONAL_TALLY), self.p("55-rating-block.md"))
        elif self.exists("55-rating-block.md"):
            # A final block from an earlier round or an earlier run must not
            # outlive a provisional tally: right now there IS no final tally,
            # and a leftover file claims otherwise.
            self.p("55-rating-block.md").unlink()
            self.hb("5", "rating-block-withdrawn",
                    f"tally n={n_target} is provisional ({decision.get('decision')})")
        for name in decision.get("malformed", []):
            self.gap(f"MISSING(judge): vote file {name} was unparseable by "
                     "ensemble.py and excluded from the tally.")
        return decision

    @staticmethod
    def _tally_is_final(decision, n_target):
        """True when no further judge round can change this tally. An n=3 round
        that escalates or backfills is followed by slots 4-5; the n=5 round is
        the last one the panel has (there is no slot 6 to backfill into)."""
        return n_target >= 5 or decision.get("decision") not in NONFINAL_DECISIONS

    def _decision_json(self):
        """The last tally's decision as written to disk, or None when it is
        absent or unreadable. Used by --resume, which must NOT trust the rating
        block alone to prove stage 5 finished."""
        if not self.exists("55-decision.json"):
            return None
        try:
            decision = json.loads(self.read("55-decision.json"))
        except (OSError, ValueError):
            return None
        return decision if isinstance(decision, dict) else None

    def _assemble_bundle(self):
        """The immutable Stage-5 input, assembled ONCE. Every judge gets these
        exact bytes; nothing here is summarized, re-ordered, or repaired."""
        out = [f"# {self.ticker} judge bundle — immutable Stage 5 input", ""]
        missing = [q["role"] for q in self.quarantines]
        if missing:
            out += ["The following role(s) failed the artifact accept gate and are "
                    "EXCLUDED from this bundle: " + ", ".join(sorted(set(missing))) +
                    ". Their raw output is quarantined in the run folder and named in "
                    "Data Gaps. Do not infer that the surviving side won by concession.",
                    ""]
        out += ["## DATA PACK", "", self._pack_md().rstrip("\n"), ""]
        for role in ANALYSTS:
            if self.exists(f"20-analyst-{role}.md"):
                out += [f"## {ANALYST_TITLE[role]}", "",
                        self.read(f"20-analyst-{role}.md").rstrip("\n"), ""]
            else:
                out += [f"## {ANALYST_TITLE[role]}", "",
                        f"MISSING({role}): brief rejected by the accept gate.", ""]
        out += ["## CANONICAL DEBATE", "", self.read("30-debate.md").rstrip("\n"), ""]
        out += ["## RISK OFFICER", "", self.read("40-risk.md").rstrip("\n"), ""]
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    # STAGE 6 — computed mean-reversion block, then the writer
    # ------------------------------------------------------------------

    def stage6(self):
        self.stage_start("6", "writer")
        self.run_script("scripts/render_meanrev.py", [self.p("10-datapack.json")],
                        expect=(0, 3), stdout_to="53-meanrev-block.md")
        if not self.read("53-meanrev-block.md").strip():
            self.gap("MISSING(P9): render_meanrev.py produced no block; the "
                     "Mean-Reversion section is a DATA GAP.")
        template = self.skill_dir / "references" / "report-template.md"
        # The ONLY view that may carry 15-position.* (invariant 12).
        view = self.make_view_dir(
            "6-writer",
            ["10-datapack.md", "10-datapack.json", "15-position.json", "15-position.md",
             "30-debate.md", "40-risk.md", "40-riskbox-block.md", "53-meanrev-block.md",
             "55-rating-block.md", str(template)] + self._analyst_files(),
            allow_position=True)
        pack = json.loads(self.read("10-datapack.json"))
        price_tag, freshness = self._price_tag(pack)
        prompt = self.build_prompt("writer", "writer", self._pack_md(), {
            "artifacts": self._all_artifacts_md(template),
            "position_json": (self.read("15-position.json")
                              if self.exists("15-position.json")
                              else "(no position artifact — omit the section entirely)"),
            "price_tag": price_tag, "freshness": freshness,
            "n_valid": str(self.ensemble.get("n_valid", "")),
            "TICKER": self.ticker, "as_of": self.asof,
        })
        text, res = self.worker_artifact(
            "writer", self.routing["writer"], prompt, view, TIMEOUTS["writer"],
            "report", "60-report-malformed.md", "writer", {"stage": "writer"})
        if text is None:
            self.stage_end("failed", "writer artifact rejected twice")
            raise DriverError("stage6-no-report",
                              "the writer's output failed the accept gate twice; there "
                              "is no report to QA or publish (raw at "
                              "60-report-malformed.md)")
        self.write("60-report.md", text.strip() + "\n")
        self._append_data_gaps()
        self.stage_end("ok")

    # ------------------------------------------------------------------
    # STAGE 7 — QA, one fix pass, then the mechanical footer patch
    # ------------------------------------------------------------------

    def stage7(self):
        self.stage_start("7", "qa")
        prose = self._prose_qa()
        first = self._qa_check(footer=False, out_rel="70-qa-prefooter.txt")
        self.qa = {"prose_qa_clean": prose.strip().upper().startswith("PROSE QA: CLEAN"),
                   "attempt1_exit": first.returncode}
        if first.returncode != 0:
            self.hb("7", "qa-failed", "running the single permitted fix pass")
            self.p("70-qa-prefooter.txt").rename(self.p("70-qa-prefooter-attempt1.txt"))
            self._qa_fix_pass(self.read("70-qa-prefooter-attempt1.txt"))
            second = self._qa_check(footer=False, out_rel="70-qa-prefooter.txt")
            self.qa["attempt2_exit"] = second.returncode
            if second.returncode != 0:
                # Existing rule, unchanged: ship with a QA-exceptions box quoting
                # the failures verbatim. Never silently.
                self._insert_qa_exceptions(self.read("70-qa-prefooter.txt"))
                self.qa["qa_exceptions_box"] = True
                self.gap("QA EXCEPTIONS: qa_check.py hard-failed twice; the failing "
                         "lines are quoted verbatim in the report's QA exceptions "
                         "section (SKILL.md failure map).")
                self.stage_end("degraded", "qa_check failed twice -> QA-exceptions box")
                return
        self.stage_end("ok")

    def _prose_qa(self):
        view = self.make_view_dir("7-prose-qa", ["60-report.md", "10-datapack.json"])
        prompt = self.build_prompt("prose_qa", "prose_qa", self._pack_md(), {
            "report": self.read("60-report.md"),
            "datapack_json": self.read("10-datapack.json")})
        text, res = self.worker_artifact(
            "prose_qa", self.routing["qa"], prompt, view, TIMEOUTS["prose_qa"],
            None, None, "prose-qa", {"stage": "qa-prose"})
        if text is None:
            # SKILL.md: a missing/empty prose artifact is a hard Stage-7 failure —
            # the pass cannot be proven to have run. Fail loud rather than write a
            # fake "clean".
            raise DriverError("prose-qa-missing",
                              "the prose-QA pass produced no artifact after two spawns; "
                              "qa_check.py --prose-qa would hard-fail and a synthesized "
                              "'clean' would be a fabricated gate")
        self.write("70-qa-prose.txt", text.strip() + "\n")
        return text

    def _qa_flags(self, footer):
        args = [self.p("60-report.md"), self.p("10-datapack.json")]
        if self.exists("15-position.json"):
            args.append(self.p("15-position.json"))
        args += ["--strict"]
        if self.exists("30-debate.md"):
            args += ["--debate", self.p("30-debate.md")]
        for role in ANALYSTS:
            if self.exists(f"20-analyst-{role}.md"):
                args += ["--brief", self.p(f"20-analyst-{role}.md")]
        args += ["--prose-qa", self.p("70-qa-prose.txt")]
        if footer:
            args.append("--check-footer")
        return args

    def _qa_check(self, footer, out_rel):
        proc = self.run_script("scripts/qa_check.py", self._qa_flags(footer),
                               expect=(0, 1), stdout_to=out_rel)
        self.hb("7", "qa-check", f"{'with' if footer else 'without'}-footer "
                                 f"exit={proc.returncode}")
        return proc

    def _qa_fix_pass(self, qa_output):
        """Exactly ONE fix pass (SKILL.md). The fix worker sees the report, the
        pack and the QA failures — never 15-position.*."""
        view = self.make_view_dir("7-qa-fix", ["60-report.md", "10-datapack.json",
                                               "70-qa-prefooter-attempt1.txt",
                                               "70-qa-prose.txt"])
        prompt = "\n".join([
            "ROLE: qa_fix", f"RUN: {self.run_id}",
            "You are running HEADLESS in a read-only workspace. Your ENTIRE stdout "
            "becomes the corrected report. Emit the FULL corrected markdown report "
            "and nothing else — no preamble, no diff, no commentary. Do not write "
            "any file.",
            "",
            "Mission: fix ONLY the QA failures listed below in the report that "
            "follows. Every other byte stays as the writer wrote it. Do not "
            "re-argue the thesis, do not change the rating, and do not edit "
            "anything inside a `<!-- rating-block ... -->`, `<!-- riskbox-block "
            "... -->` or `<!-- meanrev-block ... -->` region (those are computed "
            "and inserted verbatim — invariants 1 and 16). Leave "
            "{{agent_count}}/{{model_mix}}/{{wall_s}}/{{cost_usd}} as literal "
            "unfilled tokens; Stage 7c fills them mechanically.",
            "", "== QA FAILURES (verbatim) ==", qa_output.strip(),
            "", "== PROSE QA (verbatim) ==", self.read("70-qa-prose.txt").strip(),
            "", "== REPORT TO FIX ==", self.read("60-report.md"),
        ])
        text, res = self.worker_artifact(
            "qa_fix", self.routing["qa"], prompt, view, TIMEOUTS["qa_fix"],
            "report", "60-report-fix-malformed.md", "qa-fix", {"stage": "qa-fix"})
        if text is None:
            self.gap("MISSING(qa-fix): the QA fix pass produced no usable report; the "
                     "original report stands and its QA failures are disclosed.")
            return
        self.write("60-report.md", text.strip() + "\n")
        self._append_data_gaps()

    def stage7c(self):
        """Mechanical disclosure patch, then the footer-aware QA pass."""
        self.stage_start("7c", "footer-patch")
        # run_stats prefers DRIVER-STATE.json's wall_s; publish the elapsed time
        # AS OF THIS MOMENT so the footer figure is a real measurement rather
        # than an mtime span that keeps widening as later files land.
        self.write_state(exit_code=None, status="running")
        stats = self.run_script("scripts/run_stats.py", [self.run_dir, "--json"])
        self.write("71-run-stats.json", stats.stdout)
        self.run_script("scripts/run_stats.py",
                        [self.run_dir, "--patch", self.p("60-report.md")])
        final = self._qa_check(footer=True, out_rel="70-qa-final.txt")
        self.qa["final_exit"] = final.returncode
        if final.returncode != 0:
            self.qa["final_failed"] = True
            self.gap("QA EXCEPTIONS: the post-patch qa_check.py --check-footer pass "
                     "still fails; see 70-qa-final.txt.")
            if not self.qa.get("qa_exceptions_box"):
                self._insert_qa_exceptions(self.read("70-qa-final.txt"))
                self.qa["qa_exceptions_box"] = True
            self.stage_end("degraded", f"qa_check --check-footer exit {final.returncode}")
            return
        self.stage_end("ok")

    def stage7b(self):
        self.stage_start("7b", "render")
        self.run_script("scripts/render_report.py",
                        [self.p("60-report.md"), self.p("60-report.html")])
        self.stage_end("ok")

    # ------------------------------------------------------------------
    # finish
    # ------------------------------------------------------------------

    def finish(self):
        self.stage_start("8", "finish")
        self.write(RECEIPTS_FILE, json.dumps(
            {"run_id": self.run_id, "driver_version": DRIVER_VERSION,
             "mode": "mock" if self.mock else "live",
             "calls": self.call_records}, indent=2) + "\n")
        self.write(MANIFEST_FILE, self._manifest())
        self.write("80-ledger-row.json", json.dumps(self._ledger_row(), indent=2) + "\n")
        self.stage_end("ok")

    def _ledger_row(self):
        """The INTENDED Stage-8 ledger row. The driver does not append it —
        that stays the orchestrator's gated step (and the canonical ledger lives
        in the vault, which this driver never writes)."""
        dec = self.ensemble or {}
        dist = {k: 0 for k in ("StrongSell", "Sell", "Hold", "Buy", "StrongBuy")}
        try:
            block = self.read("55-rating-block.md")
        except OSError as exc:
            # FAIL LOUD, NEVER DEGRADE. Swallowing this published an all-zero
            # vote distribution as if it were the ensemble's real one.
            raise DriverError(
                "rating-block-unreadable",
                f"{self.p('55-rating-block.md')}: {exc}; the ledger row's vote "
                "distribution cannot be filled honestly")
        for name in dist:
            m = re.search(rf"^\|\s*{name}\s*\|\s*\d+\s*\|\s*(\d+)\s*\|", block, re.M)
            if m:
                dist[name] = int(m.group(1))
        no_call = dec.get("decision") == "no-call"
        stats = {}
        if self.exists("71-run-stats.json"):
            try:
                stats = json.loads(self.read("71-run-stats.json"))
            except ValueError:
                stats = {}
        row = {
            "run_id": self.run_id, "ticker": self.ticker,
            "date_utc": now_iso().replace("+00:00", "Z"), "as_of": self.asof,
            "job": "J1 full single-ticker equity research (no --options)",
            "mode_rating": "NO-CALL" if no_call else dec.get("mode_label"),
            "distribution": dist, "spread": dec.get("spread"),
            "no_call": no_call, "gaps": list(self.data_gaps),
            "report_path": f"reports/single-ticker/{self.ticker}/"
                           f"{self.ticker}-{self.asof}.md",
            "cost_usd": stats.get("cost_usd", "not estimated"),
            "wall_s": round(time.time() - self.started, 1),
            "n_valid": dec.get("n_valid"), "n_target": dec.get("n_target"),
            "mean_conviction": dec.get("mean_conviction"),
            "judge_mix": dec.get("judge_mix", []),
            "position_aware": self.exists("15-position.json"),
            "artifact": "local-html",
            "_note": "INTENDED row — pipeline_driver.py never appends to the ledger; "
                     "Stage 8 is the orchestrator's gated step.",
        }
        # A row whose headline rating, spread, or panel size is UNKNOWN is not a
        # publishable row — it is a driver defect. ledger.py's key-presence check
        # would happily accept it into the canonical track record, and the
        # orchestrator appends on exit 0 without reading the values, so the only
        # place this can be caught honestly is here.
        unknown = [k for k in ("mode_rating", "spread", "n_valid") if row.get(k) is None]
        if unknown:
            raise DriverError(
                "ledger-row-incomplete",
                f"ledger row for {self.run_id} has null {unknown}; the stage-5 "
                f"decision is missing or unfinished (ensemble={json.dumps(dec)[:200]}). "
                "A run whose headline rating is unknown must never reach the "
                "track-record ledger.")
        return row

    def _manifest(self):
        lines = [f"# Run manifest — {self.run_id}", "",
                 f"- Driver: `pipeline_driver.py` v{DRIVER_VERSION} "
                 f"({'MOCK' if self.mock else 'live'} mode)",
                 f"- Started: {self.started_iso} · Ended: {now_iso()} "
                 f"· Wall: {round(time.time() - self.started, 1)}s",
                 f"- Routing: `{json.dumps(self.routing, sort_keys=True)}`", "",
                 "| Artifact | Bytes | Description |", "|---|---|---|"]
        for path in sorted(self.run_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.run_dir).as_posix()
            key = rel if rel in ARTIFACT_DESCRIPTIONS else path.name
            desc = ARTIFACT_DESCRIPTIONS.get(key)
            if desc is None:
                if re.match(r"^50-votes/vote-\d+\.md$", rel):
                    desc = "Stage 5 judge vote (BACKEND/MODEL/SLOT headers + verdict line)."
                elif rel.endswith("-malformed.md"):
                    desc = "QUARANTINED raw worker output — rejected by validate_artifact.py, never fed downstream."
                elif rel.endswith("-attempt1.txt"):
                    desc = "First-attempt QA output, kept because a fix pass followed."
                else:
                    desc = "(undescribed artifact)"
            lines.append(f"| `{rel}` | {path.stat().st_size} | {desc} |")
        if self.quarantines:
            lines += ["", "## Quarantines", ""]
            lines += [f"- **{q['role']}** → `{q['quarantined_to']}`: "
                      + "; ".join(q["reasons"])[:300] for q in self.quarantines]
        if self.data_gaps:
            lines += ["", "## Data gaps", ""] + [f"- {g}" for g in self.data_gaps]
        return "\n".join(lines) + "\n"

    def write_state(self, exit_code, status, reason=None):
        # A terminal state write means the run is over: the stage view dirs
        # (one of which holds the writer's copy of 15-position.*) must not
        # outlive it, on the failure paths as much as on the clean one.
        if exit_code is not None:
            self.cleanup_views()
        wall = round(time.time() - self.started, 1)
        # The row is emitted here for the orchestrator, but a run that FAILED
        # mid-flight may legitimately have no honest row yet. Report that as an
        # explicit error rather than either fabricating a row or losing the whole
        # state file to a second exception on an error path.
        ledger_row, ledger_row_error = None, None
        if self.exists("55-rating-block.md"):
            try:
                ledger_row = self._ledger_row()
            except DriverError as exc:
                ledger_row_error = {"code": exc.code, "detail": exc.detail}
        state = {
            "schema": 1, "driver_version": DRIVER_VERSION,
            "run_id": self.run_id, "ticker": self.ticker, "as_of": self.asof,
            "run_dir": str(self.run_dir),
            "mode": "mock" if self.mock else "live",
            "worker_cmd": self.worker_cmd[0],
            "routing": self.routing,
            "driver_started_at": self.started_iso,
            "driver_ended_at": now_iso(),
            "wall_s": wall,
            "status": status, "exit_code": exit_code,
            "reason": reason,
            "stages": [{k: v for k, v in s.items() if k != "started_s"}
                       for s in self.stages],
            "quarantines": self.quarantines,
            "data_gaps": self.data_gaps,
            "view_dirs": self.view_dirs,
            "view_dirs_removed": self.views_removed,
            "position_view_violations": self.position_view_violations,
            "position_views_ok": not self.position_view_violations,
            "ensemble": self.ensemble,
            "judge_rounds": self.rounds,
            "qa": self.qa,
            "notes": self.notes,
            "receipt_count": len(self.call_records),
            "ledger_row": ledger_row,
            "ledger_row_error": ledger_row_error,
            "ledger_appended": False,
            "vault_copied": False,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.p(STATE_FILE).write_text(json.dumps(state, indent=2) + "\n",
                                      encoding="utf-8")
        return state

    # --- small helpers -----------------------------------------------------

    def _pack_md(self):
        if self.exists("10-datapack.md"):
            return self.read("10-datapack.md")
        return self.read("10-datapack.json")

    def _analyst_files(self):
        return [f"20-analyst-{r}.md" for r in ANALYSTS if self.exists(f"20-analyst-{r}.md")]

    def _analyst_briefs_md(self):
        out = []
        for role in ANALYSTS:
            if self.exists(f"20-analyst-{role}.md"):
                out += [f"### {ANALYST_TITLE[role]}", "",
                        self.read(f"20-analyst-{role}.md").rstrip("\n"), ""]
            else:
                out += [f"### {ANALYST_TITLE[role]}", "",
                        f"MISSING({role}): brief rejected by the accept gate.", ""]
        return "\n".join(out)

    def _all_artifacts_md(self, template):
        out = []
        for rel in ["10-datapack.md"] + self._analyst_files() + [
                "30-debate.md", "40-risk.md", "53-meanrev-block.md",
                "55-rating-block.md"]:
            if self.exists(rel):
                out += [f"===== {rel} =====", self.read(rel).rstrip("\n"), ""]
        if self.exists("15-position.json"):
            out += ["===== 15-position.json =====", self.read("15-position.json").rstrip("\n"), ""]
        try:
            out += ["===== references/report-template.md =====",
                    Path(template).read_text(encoding="utf-8").rstrip("\n"), ""]
        except OSError as exc:
            raise DriverError("report-template-unreadable", f"{template}: {exc}")
        return "\n".join(out)

    def _price_tag(self, pack):
        """Which price tag the writer may cite (invariant 11). Never invent a
        tag the pack does not carry."""
        last = pack.get("P1.last")
        if isinstance(last, dict) and last.get("v") is not None:
            rt = pack.get("P1.is_realtime")
            fresh = "real-time"
            if isinstance(rt, dict) and rt.get("v") is False:
                fresh = "DELAYED"
            if isinstance(last.get("asof"), str) and last["asof"][:10] < self.asof:
                fresh = f"STALE: last trade {last['asof'][:10]}"
            return "[P1.last]", fresh
        return "[P1.price]", "settled close"

    def _headers(self, res, role):
        return ("BACKEND: cursor\n"
                f"MODEL: {(res or {}).get('cliModel') or '(unknown)'}\n"
                f"ROLE: {role}\n"
                f"RECEIPT: {(res or {}).get('receipt_path') or '(none)'}\n\n")

    def _call_record(self, res, **extra):
        """One census row. Key names deliberately mirror what run_stats.py's
        `_one_entry` reads (`stage`/`role`/`cliModel`/`receipt`/`exitCode`/
        `startedAtMs`/`durationMs`) so the disclosure footer is computed from
        this file rather than re-derived."""
        res = res or {}
        rec = {"stage": res.get("stage"), "slot": res.get("slot"),
               "role": res.get("role"), "model": res.get("model_requested"),
               "cliModel": res.get("cliModel"), "cli": res.get("cli"),
               "receipt": res.get("receipt_path"),
               "exitCode": res.get("exit_code"),
               "startedAtMs": res.get("started_ms"),
               "durationMs": res.get("duration_ms"),
               "attempt": res.get("attempt"),
               "accepted": bool(res.get("ok")) and not res.get("accept_reasons"),
               "promptSha256": res.get("promptSha256"),
               "outputSha256": res.get("outputSha256")}
        rec.update({k: v for k, v in extra.items() if v is not None})
        return rec

    def _append_data_gaps(self):
        """Mechanically guarantee every quarantine reaches the report's Data gaps
        section. The writer is TOLD about them, but a prompt is not a gate: the
        undisclosed-malformed-brief defect this driver exists to prevent is
        exactly a gap that depended on a model remembering to write it."""
        if not self.data_gaps:
            return
        text = self.read("60-report.md")
        m = re.search(r"^##\s+Data gaps\s*$", text, re.M | re.I)
        if not m:
            self.warn("60-report.md has no '## Data gaps' section; quarantine "
                      "disclosures were appended at the end of the report instead")
            block = "\n\n## Data gaps\n\n" + "\n".join(
                f"- {g}" for g in self.data_gaps) + "\n"
            self.write("60-report.md", text.rstrip("\n") + block)
            return
        end = text.find("\n## ", m.end())
        end = len(text) if end == -1 else end
        section, tail = text[m.end():end], text[end:]
        add = [g for g in self.data_gaps if g[:60] not in section]
        if not add:
            return
        section = section.rstrip("\n") + "\n\n" + "\n".join(
            f"- {g}" for g in add) + "\n"
        self.write("60-report.md", text[:m.end()] + section + tail)

    def _insert_qa_exceptions(self, qa_output):
        """Quote the failing QA lines verbatim into the report's QA exceptions
        section (SKILL.md failure map: qa_check hard fail x2 -> ship with the box)."""
        failures = [ln for ln in qa_output.splitlines() if ln.startswith("!")]
        body = ("\nqa_check.py hard-failed twice; the failing checks are quoted "
                "verbatim below and were NOT silently resolved.\n\n"
                + "\n".join(f"- `{ln}`" for ln in failures or ["! (see 70-qa-*.txt)"])
                + "\n\n")
        text = self.read("60-report.md")
        m = re.search(r"^##\s+QA exceptions\s*$", text, re.M | re.I)
        if not m:
            self.write("60-report.md", text.rstrip("\n") +
                       "\n\n## QA exceptions\n" + body)
            return
        end = text.find("\n## ", m.end())
        end = len(text) if end == -1 else end
        self.write("60-report.md", text[:m.end()] + body + text[end:].lstrip("\n"))

    # --- resume ------------------------------------------------------------

    # (stage key, sentinel artifact, method name). --resume stats these in order
    # and restarts at the first missing sentinel — SKILL.md's resume rule,
    # implemented natively instead of narrated.
    STAGE_PLAN = [
        ("1", "datapack", "10-datapack.json", "stage1"),
        ("2", "analysts", "20-analyst-*.md (all four, accepted or quarantined)", "stage2"),
        ("3", "debate", "30-debate.md (both cases resolved)", "stage3"),
        ("4", "risk", "40-risk.md", "stage4"),
        ("5", "ensemble", "55-rating-block.md (from a FINAL tally, per 55-decision.json)", "stage5"),
        ("6", "writer", "60-report.md", "stage6"),
        ("7", "qa", "70-qa-prefooter.txt", "stage7"),
        ("7c", "footer-patch", "70-qa-final.txt", "stage7c"),
        ("7b", "render", "60-report.html", "stage7b"),
    ]

    def stage_done(self, method):
        """SKILL.md's resume rule, native: stat this stage's artifacts and say
        whether it finished. A quarantined role counts as FINISHED — its gap is
        already recorded and re-running it would just re-quarantine it."""
        if method == "stage2":
            return all(self.exists(f"20-analyst-{r}.md")
                       or self.exists(f"20-analyst-{r}-malformed.md") for r in ANALYSTS)
        if method == "stage3":
            if not self.exists("30-debate.md"):
                return False
            debate = self.read("30-debate.md")
            return (("## Bear case" in debate or "MISSING(bear)" in debate)
                    and ("## Bull case" in debate or "MISSING(bull)" in debate))
        if method == "stage5":
            # The sentinel alone is not proof. Belt-and-braces with _tally's
            # provisional path: even if a rating block written by an older
            # driver (or a hand-run ensemble.py) is sitting there, stage 5 is
            # done only when the decision on disk is FINAL. escalate/backfill
            # means the mandatory n=5 round never completed, so the block is a
            # 3-judge provisional headline and must not be published as final.
            if not self.exists("55-rating-block.md"):
                return False
            decision = self._decision_json()
            if decision is None:
                return False
            return self._tally_is_final(decision, decision.get("n_target") or 0)
        sentinel = next(s for _, _, s, m in self.STAGE_PLAN if m == method)
        return self.exists(sentinel)

    def _resume_rehydrate(self, method):
        """Reload a SKIPPED stage's in-memory state from disk. Without this a
        resumed run carries an empty `self.ensemble` through to the ledger row,
        which then reports a null mode_rating/spread/n_valid for a run whose
        rating block on disk says otherwise."""
        if method == "stage5":
            self.ensemble = self._decision_json() or {}

    def run(self):
        # Routing is validated BEFORE anything is created on disk, so a typo in
        # routing.json cannot leave a half-made run folder behind.
        self.load_routing()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_root = tempfile.mkdtemp(prefix=f"trdrv-views-{self.run_id}-")
        self.hb("0", "driver-start",
                f"ticker={self.ticker} run_dir={self.run_dir} "
                f"mode={'MOCK' if self.mock else 'live'}")
        if self.mock:
            self.warn("MOCK MODE: stage 1 is seeded from a fixture and every worker "
                      "is the override command — no vendor and no model is called. "
                      "This run is NOT publishable.")
        resuming = self.args.resume
        for stage, name, sentinel, method in self.STAGE_PLAN:
            if resuming and self.stage_done(method):
                self.stage_skip(stage, name, f"--resume: {sentinel} already present")
                self._resume_rehydrate(method)
                continue
            resuming = False   # first missing sentinel -> run everything after it
            getattr(self, method)()
        self.finish()
        return EXIT_OK


# The driver-owned shape clauses. validate_artifact.py enforces these; the role
# cards in references/prompts.md predate the gate and do not all request them
# (its module docstring says so explicitly), so the driver must ask for them or
# the gate would quarantine correct work for a missing heading.
OUTPUT_CONTRACTS = {
    "fund": "- End with exactly ONE line starting `KEY POINTS:` — never two.",
    "tech": "- End with exactly ONE line starting `KEY POINTS:` — never two.",
    "sent": "- End with exactly ONE line starting `KEY POINTS:` — never two.",
    "meanrev": "- End with exactly ONE line starting `KEY POINTS:` — never two.",
    "bull": "- BEGIN with the exact heading line `## Bull case`.\n"
            "- Write the BULL case only. Never include a `## Bear case` heading or "
            "the bear's argument.\n"
            "- Emit your case ONCE. A second emission (restated, re-worded, or "
            "appended) is MALFORMED.\n"
            "- End with exactly ONE line starting `KEY POINTS:`.",
    "bear": "- BEGIN with the exact heading line `## Bear case`.\n"
            "- Write the BEAR case only. Never reproduce the `## Bull case` section "
            "you were given — quote from it inline instead.\n"
            "- Emit your case ONCE. A second emission (restated, re-worded, or "
            "appended) is MALFORMED.\n"
            "- End with exactly ONE line starting `KEY POINTS:`.",
    "risk": "- Your FIRST bytes must be the RISK BOX block reproduced verbatim, "
            "starting with `<!-- riskbox-block: inserted verbatim, do not edit -->` "
            "and ending with `<!-- riskbox-block: end -->`, unchanged.\n"
            "- Then at least a paragraph of narration beneath it.\n"
            "- End with exactly ONE line starting `KEY POINTS:`.",
    "judge": "- The LAST line of your output must be the 4-field verdict line, and "
             "there must be exactly ONE such line in the whole response:\n"
             "  `VERDICT: <StrongSell|Sell|Hold|Buy|StrongBuy> | CONVICTION: <1-10> "
             "| ENTRY-PATH: <=15 words | WHY: <one sentence>`\n"
             "- Nothing may follow it. A missing ENTRY-PATH field makes the vote "
             "MALFORMED and it is dropped from the tally.",
    "writer": "- Emit the complete report markdown ONCE. It must contain the "
              "headings `## Executive summary`, `## Thesis`, `## Risk box`, "
              "`## Data gaps`, and `## Disclosure`.\n"
              "- Leave `{{agent_count}}`, `{{model_mix}}`, `{{wall_s}}` and "
              "`{{cost_usd}}` as literal unfilled tokens.",
}


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="pipeline_driver.py",
        description="Deterministic driver for trading-research Stages 1-7c "
                    "(single-ticker, live mode only).")
    p.add_argument("--ticker", required=True)
    p.add_argument("--run-dir", required=True,
                   help="absolute run folder; created if absent")
    p.add_argument("--routing", required=True, help="path to routing.json")
    p.add_argument("--asof", default=datetime.date.today().isoformat())
    p.add_argument("--stamp", default=datetime.datetime.now().strftime("%H%M"))
    p.add_argument("--skill-dir", default=str(SKILL_DIR))
    p.add_argument("--worker-wrapper", default="~/.agent/bin/cursor-delegate.sh")
    p.add_argument("--worker-cmd-override", default=None,
                   help="replace the delegate wrapper (implies --mock: no vendor "
                        "call, pack seeded from a fixture)")
    p.add_argument("--mock", action="store_true",
                   help="offline mock run: seed stage 1 from a fixture pack")
    p.add_argument("--datapack-fixture", default=None,
                   help="mock-mode pack fixture (default: the audited UNH run)")
    p.add_argument("--holdings", default=None,
                   help="holdings snapshot SSOT for stage 1b; default is the day's "
                        "snapshot under <skill-dir>/runs/.holdings-history")
    p.add_argument("--holdings-dir", default=None)
    p.add_argument("--resume", action="store_true",
                   help="stat artifacts in order and restart at the first missing one")
    p.add_argument("--keep-views", action="store_true",
                   help="DEBUG: keep the per-stage view dirs in system temp instead "
                        "of deleting them at exit. They contain copies of the run's "
                        "artifacts, including the writer's 15-position.* (real "
                        "holdings) — delete them yourself.")
    # Out-of-scope switches. Accepted ONLY so the rejection is explicit and
    # machine-readable instead of an argparse "unrecognized argument".
    p.add_argument("--options", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--options-only", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--replay", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--crypto", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--batch", action="store_true", help=argparse.SUPPRESS)
    return p


def check_scope(args):
    """Scope v1 mirrors the cursor host's R4: single-ticker, live only."""
    rejected = [name for name, on in (
        ("--options", args.options), ("--options-only", args.options_only),
        ("--replay", args.replay), ("--crypto", args.crypto),
        ("--batch", args.batch)) if on]
    if rejected:
        return (f"out of scope for pipeline_driver.py v1: {', '.join(rejected)}. "
                "v1 is single-ticker, live mode only — run those on the "
                "claude-code host per SKILL.md.")
    if "," in args.ticker or " " in args.ticker.strip():
        return (f"--ticker {args.ticker!r} looks like more than one ticker; v1 is "
                "single-ticker only.")
    try:
        asof = datetime.date.fromisoformat(args.asof)
    except ValueError:
        return f"--asof {args.asof!r} is not YYYY-MM-DD."
    if asof != datetime.date.today():
        return (f"--asof {args.asof} is not today; historical as-of replay is out of "
                "scope for v1 (SKILL.md '## Historical as-of replay' stays "
                "claude-code-only).")
    if not os.path.isabs(args.run_dir):
        return f"--run-dir must be absolute, got {args.run_dir!r}."
    return None


def main(argv=None):
    args = build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])
    problem = check_scope(args)
    if problem:
        sys.stderr.write(f"pipeline_driver.py: {problem}\n")
        return EXIT_BAD_INVOCATION

    driver = Driver(args)
    try:
        return _run_driver(driver)
    finally:
        # Last net: write_state() already cleans up on every terminal write, but
        # a run whose state write itself blew up must still not leave copies of
        # 15-position.* in system temp.
        driver.cleanup_views()


def _run_driver(driver):
    try:
        code = driver.run()
        # Exit 0 still means "artifacts complete, Stage 8 is yours" — but a run
        # that shipped a QA-exceptions box must SAY so in its status, not hide
        # behind a clean-looking label.
        status = ("published-ready-with-qa-exceptions"
                  if driver.qa.get("qa_exceptions_box") else "published-ready")
        state = driver.write_state(exit_code=code, status=status)
    except DriverError as exc:
        if exc.exit_code == EXIT_BAD_INVOCATION:
            sys.stderr.write(f"pipeline_driver.py: {exc.code}: {exc.detail}\n")
            return EXIT_BAD_INVOCATION
        status = "abstain" if exc.exit_code == EXIT_ABSTAIN else "needs-orchestrator"
        driver.hb("-", status, exc.code)
        sys.stderr.write(f"pipeline_driver.py: {exc.code}: {exc.detail}\n")
        try:
            driver.write_state(exit_code=exc.exit_code, status=status,
                               reason={"code": exc.code, "detail": exc.detail})
        except Exception as inner:                                   # noqa: BLE001
            sys.stderr.write(f"pipeline_driver.py: could not write {STATE_FILE}: "
                             f"{inner}\n")
        return exc.exit_code
    except Exception as exc:                                          # noqa: BLE001
        import traceback
        traceback.print_exc()
        try:
            driver.write_state(exit_code=EXIT_NEEDS_ORCH, status="needs-orchestrator",
                               reason={"code": "driver-crash",
                                       "detail": f"{type(exc).__name__}: {exc}"})
        except Exception:                                             # noqa: BLE001
            pass
        return EXIT_NEEDS_ORCH

    driver.hb("-", status,
              f"wall={state['wall_s']}s gaps={len(driver.data_gaps)} "
              f"quarantines={len(driver.quarantines)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
