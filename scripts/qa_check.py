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
    r"([~≈])?\s*(?<![A-Za-z0-9.\-])(-?\$?-?\d[\d,]*(?:\.\d+)?)\s*(%)?\s*\[([PH]\d+)\.([A-Za-z0-9_]+)\]")
DERIVED_RE = re.compile(
    r"derived\s*\(\s*([PH]\d+\.[A-Za-z0-9_]+)\s*([x×*/])\s*([PH]\d+\.[A-Za-z0-9_]+)\s*\)")
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
        try:
            num = to_float(m.group(2))
        except ValueError:
            continue  # a malformed token (e.g. '--5') the regex over-captured
        has_pct = bool(m.group(3))
        fact_id = f"{m.group(4)}.{m.group(5)}"  # group 4 carries the prefix (P2 / H1)
        if fact_id not in pack:
            results.append((False, f"FAIL {fact_id}: tag has no pack entry"))
            continue
        fv = pack[fact_id]["v"]
        if fv is None:
            continue  # fact carries no value: not a numeric claim
        if isinstance(fv, str):
            continue  # dates/labels: not a numeric claim
        if isinstance(fv, (list, dict)):
            results.append((False, f"FAIL {fact_id}: non-scalar fact numerically "
                                   f"tagged (context-only, cite without a number)"))
            continue
        v = float(fv)
        # Unit-aware: a %-suffixed cite of a unit:ratio fact is stated as a percent
        # (109.1% == ratio 1.091) — compare num/100. Fixes the P4.atm_iv_near trap.
        cmp = num / 100 if has_pct and pack[fact_id].get("unit") == "ratio" else num
        tol = 0.05 if approx else 0.005
        rel = abs(cmp - v) / abs(v) if v else abs(cmp - v)
        if rel <= tol:
            results.append((True, f"PASS {fact_id}: {num}{'%' if has_pct else ''} matches {v}"))
        else:
            results.append((False, f"FAIL {fact_id}: report {num}{'%' if has_pct else ''} "
                                   f"vs pack {v} (rel {rel:.2%} > {tol:.1%})"))
    return results


def recompute_derived(pack):
    """Recompute each fact whose `src` self-declares `derived (A op B)` from its
    constituents and compare to the stored `v` (0.5%). Catches internally
    inconsistent derived numbers a stored-value match would miss. Hard-fail when
    both constituents are present scalars; warn (never fail) when one is missing
    or non-scalar. Only `derived (...)`-tagged facts are touched — schwab-native
    ratios/percents (e.g. P2.atr14_pct) are base-ambiguous and left alone.
    Returns (results, warnings)."""
    results, warnings = [], []

    def scalar(fid):
        f = pack.get(fid)
        if not isinstance(f, dict):
            return None
        val = f.get("v")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        return float(val)

    for fid, fact in pack.items():
        if not isinstance(fact, dict):
            continue
        m = DERIVED_RE.search(str(fact.get("src", "")))
        if not m:
            continue
        v = scalar(fid)
        if v is None:
            continue
        a, op, b = scalar(m.group(1)), m.group(2), scalar(m.group(3))
        if a is None or b is None or (op == "/" and b == 0):
            warnings.append(f"derived {fid}: cannot recompute "
                            f"({m.group(1)} {op} {m.group(3)} — missing/non-scalar constituent)")
            continue
        rec = a * b if op in "x×*" else a / b
        rel = abs(rec - v) / abs(v) if v else abs(rec - v)
        if rel <= 0.005:
            results.append((True, f"PASS derived {fid}: {v:g} ≈ {m.group(1)}{op}{m.group(3)}"))
        else:
            results.append((False, f"FAIL derived {fid}: stored {v:g} vs recomputed "
                                   f"{rec:g} from {m.group(1)}{op}{m.group(3)} (rel {rel:.2%})"))
    return results, warnings


# Verbatim regions whose numbers are script-computed and intentionally untagged.
# (start-substring, end-line-predicate). An UNTERMINATED start is NOT exempted —
# fail-safe: a truncated block must never swallow (hide) the rest of the report.
RATING_BLOCK = ("rating-block: inserted verbatim",
                lambda ln: ln.strip().startswith("_Actual N:"))
RISKBOX_BLOCK = ("riskbox-block: inserted verbatim",
                 lambda ln: "riskbox-block: end" in ln)


def _block_line_indices(lines, blocks):
    """Set of line indices inside WELL-FORMED (matched start...end) verbatim
    blocks. A start with no matching end contributes nothing (fail-safe)."""
    exempt = set()
    for start_key, end_pred in blocks:
        i, n = 0, len(lines)
        while i < n:
            if start_key in lines[i]:
                j = i + 1
                while j < n and not end_pred(lines[j]):
                    j += 1
                if j < n:                       # found the end
                    exempt.update(range(i, j + 1))
                    i = j + 1
                    continue
            i += 1
    return exempt


def strip_riskbox(text):
    """Drop only well-formed verbatim risk-box regions (risk_box.py output). Its
    derived numbers carry no tag by design and are correct by construction —
    exempt from the cite check. An unterminated block is left in (fail-safe)."""
    lines = text.splitlines()
    exempt = _block_line_indices(lines, [RISKBOX_BLOCK])
    return "\n".join(ln for k, ln in enumerate(lines) if k not in exempt)


def scan_untagged(text, sections=CHECK_SECTIONS):
    """Flag numbers lacking a [P#.fact] tag inside judgment sections, unless
    the line carries a URL. Returns list of warning strings (non-fatal). Skips
    the well-formed verbatim regions (rating block, risk box) whose numbers are
    script-computed and intentionally untagged; an unterminated block is still
    scanned (fail-safe)."""
    warnings = []
    lines = text.splitlines()
    exempt = _block_line_indices(lines, [RATING_BLOCK, RISKBOX_BLOCK])
    section_on = False
    for idx, line in enumerate(lines):
        if idx in exempt:
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
    report = report.replace("−", "-")  # typographic minus (−) → ASCII so negatives parse
    with open(argv[1]) as f:
        pack = json.load(f)
    if len(argv) >= 3 and argv[2] and os.path.exists(argv[2]):
        with open(argv[2]) as f:
            pack = {**pack, **json.load(f)}  # position facts (15-position.json): a 2nd tag source
    # An absent position file is normal (flat / back-dated / auth-fail runs write none):
    # skip the merge, never crash — a stray [H#] tag then fails as "no pack entry".
    results = check_pairs(strip_riskbox(report), pack)
    dresults, dwarnings = recompute_derived(pack)
    results += dresults
    warnings = scan_untagged(report) + dwarnings
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
