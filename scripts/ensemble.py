#!/usr/bin/env python3
"""Tally judge votes into the verbatim rating block. Stdlib only.
Usage: ensemble.py tally <votes_dir> --n-target {3,5}
Emits the rating-block markdown on stdout and a JSON decision line on stderr."""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

NOTCH = {"StrongSell": 1, "Sell": 2, "Hold": 3, "Buy": 4, "StrongBuy": 5}
LABEL = {v: k for k, v in NOTCH.items()}
VERDICT_RE = re.compile(
    r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)\s*\|\s*"
    r"CONVICTION:\s*(\d+)\s*\|\s*ENTRY-PATH:\s*(.+?)\s*\|\s*WHY:\s*(.+?)\s*$")
HEADER_RE = re.compile(r"(BACKEND|MODEL|SLOT):\s*(.*)$")
DEFAULT_MODEL = "claude/opus"


def parse_headers(lines):
    """Consume leading BACKEND:/MODEL:/SLOT: header lines (a Cursor-host vote file
    prefixes them; SLOT is informational). Returns (model, body_lines); model
    defaults to claude/opus when no leading MODEL: header (legacy Claude Code)."""
    model, i = DEFAULT_MODEL, 0
    for ln in lines:
        m = HEADER_RE.match(ln)
        if not m:
            break
        if m.group(1) == "MODEL":
            model = m.group(2).strip() or model
        i += 1
    return model, lines[i:]


def parse_vote(path):
    """Return (notch, conviction, entry_path, why, verbatim_line, model) or
    None if malformed. Leading header lines are consumed first; a header-only
    file (no VERDICT body) is malformed. A vote missing the ENTRY-PATH field
    (e.g. a stale 3-field vote) is malformed — it never silently degrades to
    a 3-field parse.

    A file containing MORE THAN ONE VERDICT line is malformed. Reading only
    body[-1] would silently count the last-appended block, so a vote file that
    got written twice (e.g. a delegate retry, or a fallback rung appending to a
    path a previous rung already wrote) would have its verdict decided by append
    order. Observed 2026-07-18: 23/32 slot-1 votes carried duplicate blocks, and
    NASA (Hold->Sell) and XLE (Hold->Buy) self-disagreed across blocks, deciding
    two headline ratings by concatenation order. Fail loud instead: the caller's
    existing respawn-once path handles it."""
    lines = [ln.rstrip() for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return None
    model, body = parse_headers(lines)
    if not body:
        return None
    if sum(1 for ln in body if ln.strip().startswith("VERDICT:")) != 1:
        return None
    m = VERDICT_RE.match(body[-1].strip())
    if not m:
        return None
    conv = int(m.group(2))
    if not 1 <= conv <= 10:
        return None
    return (NOTCH[m.group(1)], conv, m.group(3).strip(), m.group(4).strip(),
            body[-1].strip(), model)


def collect(votes_dir):
    """Return (votes, malformed_filenames) from vote-*.md in the dir. Lexical
    sort is correct for N<=9; the ensemble never exceeds N=5."""
    votes, malformed = [], []
    for vp in sorted(Path(votes_dir).glob("vote-*.md")):
        parsed = parse_vote(vp)
        (malformed.append(vp.name) if parsed is None else votes.append(parsed))
    return votes, malformed


def decide(spread, n_valid, n_target, n_malformed=0):
    """Map (spread, n_valid) to publish / escalate / backfill / no-call.

    A8 (2026-07-18): a thin panel caused by MALFORMED votes is an infrastructure
    failure, not judge disagreement, and the two states deserve different answers.
    Previously both fell through `n_valid < 3 -> no-call`, so one unparseable vote
    threw away two perfectly good judgments and killed the ticker. Now, when the
    panel is short ONLY because votes were malformed and at least one valid vote
    survives, emit `backfill`: the orchestrator spawns replacement judges in the
    unused slots (4, 5) to reach n_target, exactly as `escalate` already does.
    Fail-closed is preserved -- if backfill cannot reach 3 valid votes, the next
    tally still returns no-call. n_malformed defaults to 0, so every existing
    caller keeps its current behaviour.
    """
    if n_valid < 3:
        if n_malformed > 0 and n_valid > 0:
            return "backfill"
        return "no-call"
    if n_target >= 5:
        return "no-call" if spread >= 3 else "publish"
    return "escalate" if spread >= 2 else "publish"


def mode_notch(notches):
    counts = Counter(notches)
    top = max(counts.values())
    tied = [n for n, c in counts.items() if c == top]
    mean = statistics.mean(notches)
    return min(tied, key=lambda n: (abs(n - mean), n))


def render(votes, malformed, n_target):
    n_valid = len(votes)
    judge_mix = [v[5] for v in votes]
    counts = Counter(v[0] for v in votes)
    spread = (max(counts) - min(counts)) if votes else 0
    decision = decide(spread, n_valid, n_target, len(malformed))
    notches = [v[0] for v in votes]
    mode = mode_notch(notches) if votes else None
    median_notch = float(statistics.median(notches)) if votes else None
    mean_notch = round(statistics.mean(notches), 1) if votes else None
    mean_conv = round(statistics.mean(v[1] for v in votes), 1) if votes else 0.0
    if decision == "no-call":
        head = (f"**NO-CALL** — thin ensemble (N={n_valid} valid < 3)"
                if n_valid < 3 else
                "**NO-CALL** — unresolved split (spread ≥ 3 at N=5)")
    elif decision == "escalate":
        head = f"**{LABEL[mode]}** — provisional, escalating to N=5"
    elif decision == "backfill":
        head = (f"**{LABEL[mode]}** — PROVISIONAL, thin panel (N={n_valid} valid, "
                f"{len(malformed)} malformed); backfilling replacement judges")
    else:
        head = f"**{LABEL[mode]}**"

    out = ["<!-- rating-block: inserted verbatim, do not edit -->",
           f"### Ensemble Rating: {head}", "",
           "| Rating | Notch | Votes |", "|---|---|---|"]
    out += [f"| {LABEL[n]} | {n} | {counts.get(n, 0)} |" for n in range(1, 6)]
    if votes:
        med_str = (f"{LABEL[int(median_notch)]} ({median_notch:.1f})"
                   if median_notch.is_integer() and 1 <= median_notch <= 5
                   else f"{median_notch:.1f}")
        out += ["", f"Central tendency: mode {LABEL[mode]} · median {med_str} "
                f"· mean {mean_notch:.1f}"]
    out += ["", f"Spread: {spread} notch(es) · Mean conviction: "
            f"{mean_conv}/10 · Decision: {decision}", "",
            "**Verdicts (verbatim):**"]
    out += [f"- {v[4]}" for v in votes]
    out += ["", "**Entry paths:**"]
    out += [f"- {LABEL[v[0]]} (conviction {v[1]}): {v[2]}" for v in votes]
    if votes:
        bull = max(votes, key=lambda v: (v[0], v[1]))
        bear = min(votes, key=lambda v: (v[0], -v[1]))
        out += ["", f'**Most bullish:** "{bull[3]}"',
                f'**Most bearish:** "{bear[3]}"']
    if malformed:
        out += ["", f"_Excluded (malformed, {len(malformed)}): "
                + ", ".join(malformed) + "._"]
    # Panel line renders the actual model of each valid vote (a substituted slot
    # shows what really voted). Kept INSIDE the QA-exempt region (before the
    # `_Actual N:` terminator) so slug digits (5.5, 4.3…) never leak to scan_untagged.
    # Legacy all-opus panels stay unlabelled — Claude Code output is unchanged.
    if votes and set(judge_mix) != {DEFAULT_MODEL}:
        out += ["", f"_Panel: {' + '.join(judge_mix)}_"]
    out += ["", f"_Actual N: {n_valid} valid of {n_valid + len(malformed)} "
            f"votes (target {n_target})._"]
    decision_json = {"decision": decision, "mode": mode,
                     "mode_label": LABEL[mode] if mode else None,
                     "median_notch": median_notch, "mean_notch": mean_notch,
                     "spread": spread, "mean_conviction": mean_conv,
                     "n_valid": n_valid, "n_target": n_target,
                     "judge_mix": judge_mix, "malformed": malformed}
    return "\n".join(out) + "\n", decision_json


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tally")
    t.add_argument("votes_dir")
    t.add_argument("--n-target", type=int, choices=(3, 5), required=True)
    args = p.parse_args(argv)
    votes, malformed = collect(args.votes_dir)
    block, decision = render(votes, malformed, args.n_target)
    sys.stdout.write(block)
    sys.stderr.write(json.dumps(decision) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
