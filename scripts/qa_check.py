#!/usr/bin/env python3
"""Cite-tag QA: verify [P#.fact]-tagged numbers against the fact-id-keyed data
pack ({"P2.atr14": {"v": 19.86, ...}}) and flag untagged numbers in judgment
sections. Stdlib only. Usage: qa_check.py <report.md> <datapack.json>.
Exit 0 clean, 1 on any tagged mismatch; warnings never fail. Tolerance 0.5%
relative, relaxed to 5% for a leading ~ (approximate).
Usage: qa_check.py <report.md> <datapack.json> [position.json]. The optional
position file (15-position.json) is a second tag source for [H#.fact] tags."""
import json
import os
import re
import sys

CHECK_SECTIONS = ("rating", "thesis", "risk", "position")
TAG = r"\[[PH]\d+\.[A-Za-z0-9_]+\]"
PAIR_RE = re.compile(
    r"([~≈])?\s*(?<![A-Za-z0-9.\-])(-?\$?-?\d[\d,]*(?:\.\d+)?)\s*%?\s*\[([PH]\d+)\.([A-Za-z0-9_]+)\]")
TAG_RE = re.compile(TAG)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUM_RE = re.compile(r"(?<![A-Za-z0-9.])~?\$?-?\d[\d,]*(?:\.\d+)?%?")
URL_RE = re.compile(r"https?://|www\.")


def to_float(s):
    return float(s.replace(",", "").replace("$", "").replace("%", ""))


def check_pairs(text, pack):
    """Verify each number-then-tag pair against the pack. Returns list of
    (ok, message)."""
    results = []
    for m in PAIR_RE.finditer(text):
        approx = bool(m.group(1))
        num = to_float(m.group(2))
        fact_id = f"{m.group(3)}.{m.group(4)}"  # group 3 carries the prefix (P2 / H1)
        if fact_id not in pack:
            results.append((False, f"FAIL {fact_id}: tag has no pack entry"))
            continue
        if isinstance(pack[fact_id]["v"], str):
            continue  # dates/labels: not a numeric claim
        if isinstance(pack[fact_id]["v"], (list, dict)):
            results.append((False, f"FAIL {fact_id}: non-scalar fact numerically "
                                   f"tagged (context-only, cite without a number)"))
            continue
        v = float(pack[fact_id]["v"])
        tol = 0.05 if approx else 0.005
        rel = abs(num - v) / abs(v) if v else abs(num - v)
        if rel <= tol:
            results.append((True, f"PASS {fact_id}: {num} matches {v}"))
        else:
            results.append((False, f"FAIL {fact_id}: report {num} vs pack {v} "
                                   f"(rel {rel:.2%} > {tol:.1%})"))
    return results


def scan_untagged(text, sections=CHECK_SECTIONS):
    """Flag numbers lacking a [P#.fact] tag inside judgment sections, unless
    the line carries a URL. Returns list of warning strings (non-fatal)."""
    warnings = []
    section_on = in_block = False
    for line in text.splitlines():
        if "rating-block: inserted verbatim" in line:
            in_block = True
            continue
        if in_block:
            if line.strip().startswith("_Actual N:"):
                in_block = False
            continue
        h = re.match(r"#{1,6}\s+(.*)", line)
        if h:
            section_on = any(k in h.group(1).lower() for k in sections)
            continue
        if not section_on or URL_RE.search(line):
            continue
        ignore = [m.span() for m in PAIR_RE.finditer(line)]
        ignore += [m.span() for m in TAG_RE.finditer(line)]
        ignore += [m.span() for m in DATE_RE.finditer(line)]
        for m in NUM_RE.finditer(line):
            s = m.start()
            if any(a <= s < b for a, b in ignore):
                continue
            warnings.append(f"untagged number '{m.group().strip()}' in: "
                            f"{line.strip()[:80]}")
    return warnings


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write("usage: qa_check.py <report.md> <datapack.json> [position.json]\n")
        return 2
    with open(argv[0]) as f:
        report = f.read()
    with open(argv[1]) as f:
        pack = json.load(f)
    if len(argv) >= 3 and argv[2] and os.path.exists(argv[2]):
        with open(argv[2]) as f:
            pack = {**pack, **json.load(f)}  # position facts (15-position.json): a 2nd tag source
    # An absent position file is normal (flat / back-dated / auth-fail runs write none):
    # skip the merge, never crash — a stray [H#] tag then fails as "no pack entry".
    results = check_pairs(report, pack)
    warnings = scan_untagged(report)
    hard = [msg for ok, msg in results if not ok]
    out = ["== QA CITE CHECK =="]
    out += [("  " if ok else "! ") + msg for ok, msg in results]
    if warnings:
        out.append("== WARNINGS (non-fatal) ==")
        out += ["  " + w for w in warnings]
    out.append(f"== {len(results) - len(hard)} pass, {len(hard)} fail, "
               f"{len(warnings)} warn ==")
    sys.stdout.write("\n".join(out) + "\n")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
