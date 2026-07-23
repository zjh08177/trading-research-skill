#!/usr/bin/env python3
"""Feature 21 WS-A gates — the risk-narration ablation harness.

`ablate` (Gate 1): re-judge archived byte-identical bundles with the FULL
(archived risk-officer LLM) risk section vs a TEMPLATE (render_risk.py) risk
section, changing ONLY the `## RISK OFFICER` section, and report the ensemble
verdict FLIP-RATE and DISPERSION change. Ship WS-A iff flip-rate <= 5% AND
dispersion increase <= 10% relative.

`faithfulness` (Gate 2): mechanically diff each archived risk narration against
the box+pack and surface every fact-tag / number the narration cites that is NOT
in the computed box — the candidate "load-bearing narration" set. Zero material,
correct, non-derivable claims ⇒ WS-A is a pure deletion.

Each judge is a `cursor-delegate.sh` subprocess on byte-identical prompts,
mirroring `pipeline_driver._judge_round`; the tally reuses `ensemble.py` exactly
like production. Stdlib + repo modules only.

Usage:
  rejudge.py ablate  --archive <runs_dir> [--n N | RUN_DIR...] [--models M...]
                     [--worker PATH] [--jobs K] [--timeout S] [--out results.json]
  rejudge.py faithfulness --archive <runs_dir> [--n N | RUN_DIR...] [--out gate2.json]
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import ensemble  # noqa: E402  (NOTCH, VERDICT_RE, collect, render)

SKILL_DIR = SCRIPTS.parent
# Bundle assembly constants (mirror pipeline_driver.ANALYSTS / ANALYST_TITLE).
ANALYSTS = ("fund", "tech", "sent", "meanrev")
ANALYST_TITLE = {"fund": "FUNDAMENTAL ANALYST", "tech": "TECHNICAL ANALYST",
                 "sent": "SENTIMENT ANALYST", "meanrev": "MEAN-REVERSION ANALYST"}
DEFAULT_JUDGES = ["gpt-5.5-extra-high", "claude-opus-4-8-thinking-max", "glm-5.2-high"]
DEFAULT_WORKER = os.path.expanduser("~/.agent/bin/cursor-delegate.sh")
RISKBOX_END = "<!-- riskbox-block: end -->"
RISK_SECTION_HEADER = "## RISK OFFICER"
# render_risk.py's signature line — marks a post-cutover deterministic risk artifact.
RENDER_SIGNATURE = "templated deterministically from the box and pack (Feature 21 WS-A)"

HEADLESS_PREAMBLE = (
    "You are running HEADLESS in a read-only workspace. Your ENTIRE stdout "
    "becomes the artifact — emit the artifact text only, with no preamble, "
    "no sign-off, and no commentary about the task. Do not write, edit, or "
    "create any file; the driver persists your output. Use only the "
    "material in this prompt and the files in your workspace.")
CONTRACT_WRAPPER = ("OUTPUT CONTRACT (enforced mechanically by "
                    "scripts/validate_artifact.py — an artifact that breaks it "
                    "is quarantined, not published):")
JUDGE_CONTRACT = (
    "- The LAST line of your output must be the 4-field verdict line, and "
    "there must be exactly ONE such line in the whole response:\n"
    "  `VERDICT: <StrongSell|Sell|Hold|Buy|StrongBuy> | CONVICTION: <1-10> "
    "| ENTRY-PATH: <=15 words | WHY: <one sentence>`\n"
    "- Nothing may follow it. A missing ENTRY-PATH field makes the vote "
    "MALFORMED and it is dropped from the tally.")

TAG_RE = re.compile(r"\[([PH]\d+\.[a-zA-Z0-9_]+)\]")


# ---- role-card parsing (mirrors pipeline_driver.cards) ----------------------

def _cards():
    text = (SKILL_DIR / "references" / "prompts.md").read_text(encoding="utf-8")
    blocks = {}
    for m in re.finditer(r"^##\s+(.+?)\s*$\n+```\n(.*?)^```\s*$", text, re.M | re.S):
        blocks[m.group(1).strip()] = m.group(2)
    house = next(b for h, b in blocks.items() if h.lower().startswith("house rules"))
    judge = next(b for h, b in blocks.items() if h.lower().startswith("judge"))
    return house.rstrip(), judge.rstrip()


def build_judge_prompt(bundle, run_id):
    """Faithful replica of pipeline_driver.build_prompt('judge', ...): the whole
    bundle fills the house-rules {{datapack_md}} slot; the judge card references
    it; the judge OUTPUT CONTRACT is appended. Both arms use this identically —
    only `bundle` differs (its risk section), so the flip is attributable."""
    house, judge = _cards()
    house = house.replace("{{datapack_md}}", bundle)
    body = judge.replace("{{judge_bundle}}", "(the bundle reproduced above)")
    parts = [f"ROLE: judge", f"RUN: {run_id}", HEADLESS_PREAMBLE, "",
             house, "", body, "", CONTRACT_WRAPPER, JUDGE_CONTRACT]
    return "\n".join(parts)


# ---- bundle surgery ---------------------------------------------------------

def swap_risk_section(bundle, new_risk_body):
    """Replace everything from the final `## RISK OFFICER` header to EOF with the
    template render. RISK OFFICER is the last bundle section (see _assemble_bundle)."""
    idx = bundle.rfind("\n" + RISK_SECTION_HEADER)
    if idx == -1:
        if bundle.startswith(RISK_SECTION_HEADER):
            idx = 0
        else:
            raise ValueError("no '## RISK OFFICER' section in bundle")
    else:
        idx += 1  # keep the newline before the header
    head = bundle[:idx]
    return head + RISK_SECTION_HEADER + "\n\n" + new_risk_body.rstrip("\n") + "\n"


def _read(run_dir, name):
    p = run_dir / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def reconstruct_bundle(run_dir):
    """Rebuild the Stage-5 judge bundle from archived component artifacts, byte-
    faithful to pipeline_driver._assemble_bundle. Only 2 runs persisted a
    45-judge-bundle.md (a new codex-host artifact); reconstruction unlocks every
    archived run whose components survive. Returns None if a required component
    (pack, debate, or risk) is absent."""
    pack_md = _read(run_dir, "10-datapack.md") or _read(run_dir, "10-datapack.json")
    debate = _read(run_dir, "30-debate.md")
    risk = _read(run_dir, "40-risk.md")
    if pack_md is None or debate is None or risk is None:
        return None
    ticker = run_dir.name.split("-")[0]
    out = [f"# {ticker} judge bundle — immutable Stage 5 input", ""]
    out += ["## DATA PACK", "", pack_md.rstrip("\n"), ""]
    for role in ANALYSTS:
        brief = _read(run_dir, f"20-analyst-{role}.md")
        body = brief.rstrip("\n") if brief is not None else \
            f"MISSING({role}): brief rejected by the accept gate."
        out += [f"## {ANALYST_TITLE[role]}", "", body, ""]
    out += ["## CANONICAL DEBATE", "", debate.rstrip("\n"), ""]
    out += ["## RISK OFFICER", "", risk.rstrip("\n"), ""]
    return "\n".join(out) + "\n"


def render_template_risk(run_dir):
    """render_risk.py output for a run's pack. PACK-ONLY (invariant 12): the
    judge-bound risk artifact is position-blind, so no 15-position.json."""
    pack = run_dir / "10-datapack.json"
    args = [sys.executable, str(SCRIPTS / "render_risk.py"), str(pack)]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"render_risk.py exit {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


# ---- one judge call (mirrors _run_once, minimally) --------------------------

def run_judge(worker, model, prompt, timeout_s):
    """Invoke the delegate; return (stdout_text, ok, detail)."""
    tmp = tempfile.mkdtemp(prefix="rejudge-")
    cmd = [worker, "--dir", tmp, "--mode", "read-only", "--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt.rstrip("\n").encode("utf-8"),
                              capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, False, "timeout"
    except OSError as exc:
        return None, False, f"spawn-failed: {exc}"
    if proc.returncode != 0:
        return None, False, f"exit {proc.returncode}: {proc.stderr.decode('utf-8','replace')[-200:]}"
    return proc.stdout.decode("utf-8", "replace"), True, "ok"


def tally_arm(vote_texts, models, workdir):
    """Write votes with production headers, tally via ensemble.collect/render.
    Returns (decision_json, notches)."""
    votes_dir = pathlib.Path(workdir)
    votes_dir.mkdir(parents=True, exist_ok=True)
    for slot, (text, model) in enumerate(zip(vote_texts, models), start=1):
        if text is None:
            continue
        header = f"BACKEND: cursor\nMODEL: {model}\nSLOT: {slot}\n\n"
        (votes_dir / f"vote-{slot}.md").write_text(header + text.strip() + "\n")
    votes, malformed = ensemble.collect(votes_dir)
    _, decision = ensemble.render(votes, malformed, 3)
    notches = [v[0] for v in votes]
    return decision, notches


# ---- Gate 1: ablation -------------------------------------------------------

def _stdev(xs):
    return statistics.pstdev(xs) if len(xs) >= 2 else 0.0


def ablate(run_dirs, models, worker, jobs, timeout_s, out_path, checkpoint=None):
    # Build the two prompts per run up front.
    tasks = []   # (run_name, arm, slot, model, prompt)
    per_run = {}
    for rd in run_dirs:
        name = rd.name
        full_bundle = reconstruct_bundle(rd)
        if full_bundle is None:
            print(f"skip {name}: missing bundle components (pack/debate/risk)", file=sys.stderr)
            continue
        try:
            tmpl_bundle = swap_risk_section(full_bundle, render_template_risk(rd))
        except (ValueError, RuntimeError) as exc:
            print(f"skip {name}: {exc}", file=sys.stderr)
            continue
        per_run[name] = {"run": name}
        for arm, bundle in (("full", full_bundle), ("template", tmpl_bundle)):
            prompt = build_judge_prompt(bundle, name)
            for slot, model in enumerate(models):
                tasks.append((name, arm, slot, model, prompt))

    if not per_run:
        print("no runnable bundles", file=sys.stderr)
        return 2

    # Call-level checkpoint (JSONL): each completed judge call is appended
    # immediately, so a killed run loses nothing — a relaunch skips calls
    # already recorded. This ablation is long (opus-max judges ~200s each).
    results = {}   # (run, arm, slot) -> (text, model)
    if checkpoint and os.path.exists(checkpoint):
        with open(checkpoint) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                results[(r["run"], r["arm"], r["slot"])] = (r["text"], r["model"])
        print(f"resumed {len(results)} calls from checkpoint {checkpoint}", file=sys.stderr)
    tasks = [t for t in tasks if (t[0], t[1], t[2]) not in results]

    print(f"ablating {len(per_run)} runs × 2 arms × {len(models)} judges; "
          f"{len(tasks)} calls left to run (jobs={jobs})", file=sys.stderr)
    ck = open(checkpoint, "a") if checkpoint else None
    ck_lock = __import__("threading").Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(run_judge, worker, model, prompt, timeout_s):
                (name, arm, slot, model)
                for (name, arm, slot, model, prompt) in tasks}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            name, arm, slot, model = futs[fut]
            text, ok, detail = fut.result()
            results[(name, arm, slot)] = (text if ok else None, model)
            if ck is not None:
                with ck_lock:
                    ck.write(json.dumps({"run": name, "arm": arm, "slot": slot,
                                         "model": model, "text": text if ok else None}) + "\n")
                    ck.flush()
            done += 1
            print(f"  [{done}/{len(tasks)}] {name}/{arm}/slot{slot} {model}: {detail}",
                  file=sys.stderr)
    if ck is not None:
        ck.close()

    tmpdir = tempfile.mkdtemp(prefix="rejudge-tally-")
    flips, full_disp, tmpl_disp = 0, [], []
    counted = 0
    for name in per_run:
        row = per_run[name]
        for arm in ("full", "template"):
            texts = [results.get((name, arm, s), (None, models[s]))[0]
                     for s in range(len(models))]
            decision, notches = tally_arm(
                texts, models, os.path.join(tmpdir, name, arm))
            row[arm] = {"mode": decision["mode"], "mode_label": decision["mode_label"],
                        "spread": decision["spread"], "mean_notch": decision["mean_notch"],
                        "n_valid": decision["n_valid"], "notches": notches,
                        "stdev": round(_stdev(notches), 4),
                        "mean_conviction": decision["mean_conviction"]}
        f, t = row["full"], row["template"]
        if f["n_valid"] >= 3 and t["n_valid"] >= 3:
            counted += 1
            if f["mode"] != t["mode"]:
                flips += 1
                row["flipped"] = True
            else:
                row["flipped"] = False
            full_disp.append(f["stdev"])
            tmpl_disp.append(t["stdev"])
        else:
            row["flipped"] = None  # thin panel, excluded from rate

    flip_rate = (flips / counted) if counted else None
    mean_full = statistics.mean(full_disp) if full_disp else 0.0
    mean_tmpl = statistics.mean(tmpl_disp) if tmpl_disp else 0.0
    disp_rel = ((mean_tmpl - mean_full) / mean_full) if mean_full else (
        0.0 if mean_tmpl == 0 else float("inf"))
    gate1_pass = (flip_rate is not None and flip_rate <= 0.05 and disp_rel <= 0.10)

    summary = {
        "gate": "1-risk-narration-ablation",
        "n_runs_total": len(per_run), "n_runs_counted": counted,
        "flips": flips, "flip_rate": flip_rate,
        "dispersion_full_mean_stdev": round(mean_full, 4),
        "dispersion_template_mean_stdev": round(mean_tmpl, 4),
        "dispersion_relative_increase": (round(disp_rel, 4)
                                         if disp_rel != float("inf") else "inf"),
        "thresholds": {"flip_rate_max": 0.05, "dispersion_rel_max": 0.10},
        "GATE1_PASS": gate1_pass,
        "models": models,
        "runs": per_run,
    }
    payload = json.dumps(summary, indent=2) + "\n"
    if out_path:
        pathlib.Path(out_path).write_text(payload)
    sys.stdout.write(payload)
    return 0


# ---- Gate 2: faithfulness diff ---------------------------------------------

# The computed box only ever cites these facts (risk_box.py). Marker-independent.
BOX_FACTS = {"P1.last", "P1.price", "P1.chg_pct_1d",
             "P2.atr14", "P2.atr14_pct", "P2.sigma30", "P2.sma50"}
# render_risk.py's template legitimately cites these beyond the box.
TEMPLATE_EXTRA = {"P5.next_earnings", "H1.pct_of_book", "H1.held"}
DERIVABLE = BOX_FACTS | TEMPLATE_EXTRA


def faithfulness(run_dirs, out_path):
    """For each archived risk artifact, find every fact-tag it cites that render_risk.py
    would NOT reproduce (i.e. outside the box + template-extra set). The box only ever
    cites a fixed fact set, so any other tag necessarily came from the LLM narration —
    this is marker-independent. The DROPPED set is the candidate load-bearing-narration
    that needs a materiality+correctness judgment before WS-A ships as a pure deletion."""
    from collections import Counter
    section_hist = Counter()      # pack-section prefix (P3, P6, ...) of dropped tags
    per_run = []
    dropped_all = Counter()
    for rd in run_dirs:
        risk_p = rd / "40-risk.md"
        if not risk_p.exists():
            continue
        risk = risk_p.read_text(encoding="utf-8")
        all_tags = TAG_RE.findall(risk)
        dropped = [t for t in all_tags if t not in DERIVABLE]
        for t in dropped:
            section_hist[t.split(".")[0]] += 1
            dropped_all[t] += 1
        per_run.append({"run": rd.name, "n_tags": len(all_tags),
                        "n_dropped": len(dropped),
                        "dropped_tags": sorted(set(dropped))})
    summary = {
        "gate": "2-risk-narration-faithfulness",
        "n_runs": len(per_run),
        "definition": {"box_facts": sorted(BOX_FACTS), "template_extra": sorted(TEMPLATE_EXTRA)},
        "note": ("Fact-tags the archived risk artifact cites that render_risk.py would "
                 "DROP (outside box + template-extra). Each is a candidate non-derivable "
                 "claim needing a materiality+correctness judgment before WS-A ships as "
                 "pure deletion. High-frequency sections (P3/P6/P8) are cross-analyst "
                 "editorializing the risk box was never mandated to carry."),
        "dropped_tag_sections": dict(section_hist.most_common()),
        "dropped_tags": dict(dropped_all.most_common(50)),
        "runs": per_run,
    }
    payload = json.dumps(summary, indent=2) + "\n"
    if out_path:
        pathlib.Path(out_path).write_text(payload)
    sys.stdout.write(payload)
    return 0


# ---- run selection + CLI ----------------------------------------------------

def select_runs(archive, n, explicit):
    if explicit:
        return [pathlib.Path(p.rstrip("/")) for p in explicit]
    # A run is usable when its bundle can be RECONSTRUCTED (pack + debate + risk)
    # AND render_risk.py can run (required box facts present). Sort by mtime desc
    # so --n picks the most recent qualifying runs.
    cand = sorted((p.parent for p in pathlib.Path(archive).glob("*/40-risk.md")),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    usable = []
    for rd in cand:
        if reconstruct_bundle(rd) is None:
            continue
        # Skip post-cutover runs whose 40-risk.md is ALREADY a deterministic
        # render — swapping it for a fresh render is a no-op (guaranteed no-flip),
        # which would deflate the flip rate. The gate must sample LLM-risk runs only.
        risk = (rd / "40-risk.md").read_text(encoding="utf-8", errors="replace")
        if RENDER_SIGNATURE in risk:
            continue
        try:
            render_template_risk(rd)
        except RuntimeError:
            continue
        usable.append(rd)
        if n and len(usable) >= n:
            break
    return usable


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("ablate", "faithfulness"):
        s = sub.add_parser(cmd)
        s.add_argument("runs", nargs="*", help="explicit run dirs (else use --archive/--n)")
        s.add_argument("--archive", help="runs/ dir to auto-select from")
        s.add_argument("--n", type=int, help="how many recent bundles to sample")
        s.add_argument("--out", help="write JSON summary here too")
        if cmd == "ablate":
            s.add_argument("--models", nargs="+", default=DEFAULT_JUDGES)
            s.add_argument("--worker", default=DEFAULT_WORKER)
            s.add_argument("--jobs", type=int, default=6)
            s.add_argument("--timeout", type=int, default=600)
            s.add_argument("--checkpoint", help="JSONL: append each judge call; resume on relaunch")
    args = p.parse_args(argv)

    run_dirs = select_runs(args.archive, args.n, args.runs)
    if not run_dirs:
        print("no run dirs selected", file=sys.stderr)
        return 2
    if args.cmd == "ablate":
        return ablate(run_dirs, args.models, args.worker, args.jobs,
                      args.timeout, args.out, args.checkpoint)
    return faithfulness(run_dirs, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
