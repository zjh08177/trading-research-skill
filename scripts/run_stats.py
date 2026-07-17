#!/usr/bin/env python3
"""Deterministic disclosure-footer stats: tally agent count, model mix, and
wall-clock time from the artifacts actually present in a run folder; estimate
token cost from artifact word counts (labeled as an estimate, never claimed as
measured — no per-call token accounting is persisted anywhere in the pipeline).
Stdlib only.

Usage: run_stats.py <run_dir> [--host claude-code|cursor] [--json]
Exit 0 always (best-effort from whatever artifacts exist); 2 on bad args.
Prints the four Disclosure-footer fields (agent count, model mix, wall clock,
token cost) as a markdown fragment on stdout by default, or the same data as
JSON with --json. This does NOT compute Actual N — that stays ensemble.py's
job; the writer already sources it from 55-rating-block.md."""
import argparse
import glob
import json
import os
import re
import sys

# Model assignment is FIXED by pipeline design (SKILL.md "single source of
# truth" table), not observed at runtime — it does not vary run to run for a
# given host, so no new instrumentation is needed to report it accurately.
MODEL_TABLE = {
    "claude-code": {"analyst": "sonnet", "debate": "sonnet", "risk": "sonnet",
                     "prose_qa": "sonnet", "judge": "opus", "writer": "opus"},
    "cursor": {"analyst": "gpt-5.5-medium", "debate": "gpt-5.5-medium",
               "risk": "gpt-5.5-medium", "prose_qa": "gpt-5.5-medium",
               "judge": "opus", "writer": "opus"},
}

# Rough published per-MTok rates (USD), input/output blended down to a single
# output-weighted number since word-count estimation can't separate the two.
# This is explicitly an ESTIMATE input, not a claim of precise pricing.
RATE_PER_MTOK_OUT = {"sonnet": 15.0, "opus": 75.0, "gpt-5.5-medium": 10.0}
WORDS_PER_TOKEN = 0.75  # ~4 chars/token, ~5.3 chars/word -> rough word->token


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def count_agents(run_dir):
    """Count Agent-tool invocations from the artifacts they produced. Never
    counts the orchestrator or deterministic scripts (risk_box.py, ensemble.py,
    render_*.py) — only stages tagged 'Agent tool' in the pipeline table."""
    n = 0
    notes = []

    analyst_briefs = sorted(glob.glob(os.path.join(run_dir, "20-analyst-*.md")))
    n += len(analyst_briefs)
    notes.append(f"{len(analyst_briefs)} analyst(s)")

    debate_path = os.path.join(run_dir, "30-debate.md")
    if os.path.exists(debate_path):
        text = open(debate_path).read()
        bull = 1 if re.search(r"##\s*Bull case\b", text, re.I) else 0
        bear = 1 if re.search(r"##\s*Bear case\b", text, re.I) else 0
        n += bull + bear
        notes.append(f"{bull + bear} debate")

    risk_path = os.path.join(run_dir, "40-risk.md")
    if os.path.exists(risk_path):
        n += 1
        notes.append("1 risk officer")

    votes = sorted(glob.glob(os.path.join(run_dir, "50-votes", "vote-*.md")))
    n += len(votes)
    notes.append(f"{len(votes)} judge(s)")

    report_path = os.path.join(run_dir, "60-report.md")
    if os.path.exists(report_path):
        n += 1
        notes.append("1 writer")

    prose_path = os.path.join(run_dir, "70-qa-prose.txt")
    if os.path.exists(prose_path) and open(prose_path).read().strip():
        n += 1
        notes.append("1 QA prose pass")

    return n, "; ".join(notes), len(votes)


def wall_clock_seconds(run_dir):
    """Earliest-to-latest mtime across every artifact in the run folder —
    a real, filesystem-derived measurement, not an estimate."""
    times = []
    for path in glob.glob(os.path.join(run_dir, "**", "*"), recursive=True):
        if os.path.isfile(path):
            t = _mtime(path)
            if t is not None:
                times.append(t)
    if len(times) < 2:
        return None
    return round(max(times) - min(times), 1)

def word_count(path):
    if not os.path.exists(path):
        return 0
    return len(open(path).read().split())


def estimate_cost_usd(run_dir, models):
    """Word-count-based estimate, explicitly not a measurement. Sums output
    words per stage, converts to tokens, prices at that stage's model rate."""
    stage_words = {
        "analyst": sum(word_count(p) for p in glob.glob(os.path.join(run_dir, "20-analyst-*.md"))),
        "debate": word_count(os.path.join(run_dir, "30-debate.md")),
        "risk": word_count(os.path.join(run_dir, "40-risk.md")),
        "judge": sum(word_count(p) for p in glob.glob(os.path.join(run_dir, "50-votes", "vote-*.md"))),
        "writer": word_count(os.path.join(run_dir, "60-report.md")),
        "prose_qa": word_count(os.path.join(run_dir, "70-qa-prose.txt")),
    }
    total = 0.0
    for stage, words in stage_words.items():
        if words == 0:
            continue
        tokens = words / WORDS_PER_TOKEN
        rate = RATE_PER_MTOK_OUT.get(models[stage], 15.0)
        total += (tokens / 1_000_000) * rate
    return round(total, 3)


def build(run_dir, host="claude-code"):
    models = MODEL_TABLE.get(host, MODEL_TABLE["claude-code"])
    agent_count, agent_notes, n_judges = count_agents(run_dir)
    wall_s = wall_clock_seconds(run_dir)
    cost_usd = estimate_cost_usd(run_dir, models)
    model_mix = (f"{n_judges}x {models['judge']} (judges) + 1x {models['writer']} "
                 f"(writer) + {models['analyst']} (analysts/debate/risk/QA-prose)")
    return {
        "agent_count": agent_count,
        "agent_notes": agent_notes,
        "model_mix": model_mix,
        "wall_s": wall_s if wall_s is not None else "not recorded (fewer than 2 timestamped artifacts)",
        "cost_usd": cost_usd,
        "cost_usd_note": "estimated from output word counts, not measured token usage",
    }


def patch_report(report_path, stats):
    """Replace the writer's literal {{agent_count}}/{{model_mix}}/{{wall_s}}/
    {{cost_usd}} placeholders in the Disclosure section with computed values
    (Stage 7c, runs after both QA checks pass, before Stage 7b HTML render).
    The writer never guesses these four fields — leaves them as literal
    unfilled tokens per the writer card; this is the only thing that fills
    them. {{n_valid}} is untouched — that's ensemble.py's rating-block, not
    ours. Returns True if all four tokens were found and replaced."""
    with open(report_path) as f:
        text = f.read()
    wall = stats["wall_s"]
    wall_str = f"{wall}s" if isinstance(wall, (int, float)) else wall
    replacements = {
        "{{agent_count}}": str(stats["agent_count"]),
        "{{model_mix}}": stats["model_mix"],
        "{{wall_s}}s": wall_str,  # template carries the trailing "s" adjacent to the token
        "{{cost_usd}}": f"{stats['cost_usd']} ({stats['cost_usd_note']})",
    }
    found_all = all(token in text for token in replacements)
    for token, value in replacements.items():
        text = text.replace(token, value)
    with open(report_path, "w") as f:
        f.write(text)
    return found_all


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--host", default="claude-code", choices=sorted(MODEL_TABLE))
    p.add_argument("--json", action="store_true")
    p.add_argument("--patch", metavar="REPORT_MD",
                    help="patch {{agent_count}}/{{model_mix}}/{{wall_s}}/{{cost_usd}} "
                         "placeholders in this report file in place")
    args = p.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        sys.stderr.write(f"run_stats.py: no such run dir: {args.run_dir}\n")
        return 2

    stats = build(args.run_dir, args.host)

    if args.patch:
        if not os.path.exists(args.patch):
            sys.stderr.write(f"run_stats.py: --patch target not found: {args.patch}\n")
            return 2
        found_all = patch_report(args.patch, stats)
        if not found_all:
            sys.stderr.write(
                "run_stats.py: WARNING — not all 4 disclosure placeholders were "
                "found in the report (writer may have hand-filled or altered "
                "them); qa_check.py's footer-completeness check is authoritative.\n")
        return 0

    if args.json:
        sys.stdout.write(json.dumps(stats, indent=2) + "\n")
    else:
        wall = stats["wall_s"]
        wall_str = f"{wall}s" if isinstance(wall, (int, float)) else wall
        sys.stdout.write(
            f"Agents: {stats['agent_count']} ({stats['agent_notes']}) · "
            f"Models: {stats['model_mix']} · Wall clock: {wall_str} · "
            f"Token cost: ~${stats['cost_usd']} ({stats['cost_usd_note']}).\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
