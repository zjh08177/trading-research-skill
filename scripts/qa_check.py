#!/usr/bin/env python3
"""Cite-tag QA: verify [P#.fact]-tagged numbers against the fact-id-keyed data
pack ({"P2.atr14": {"v": 19.86, ...}}) and flag untagged numbers in judgment
sections. Stdlib only. Usage: qa_check.py <report.md> <datapack.json>.
Exit 0 clean, 1 on any tagged mismatch; warnings never fail. Tolerance 0.5%
relative, relaxed to 5% for a leading ~ (approximate).
Usage: qa_check.py <report.md> <datapack.json> [position.json]. The optional
position file (15-position.json) is a second tag source for [H#.fact] tags.
--debate <30-debate.md> additionally checks that any bull-attributed quote
in the Bear case section actually appears in the Bull case section (fabricated
strawman detector, always hard-fail, independent of --strict).
--brief <path> (repeatable) additionally scans an analyst-brief-style artifact
for a DATA GAP / MISSING / "not available" claim naming a [P#.fact] tag that
is actually present (non-null) in the pack — a hallucinated gap, always
hard-fail, independent of --strict."""
import json
import os
import re
import sys

import levels_schema
import replay
import render_report as _render_report_mod

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
REPORT_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+")

# Phrases that leak live/web-search content into a historical replay report.
# NOTE: deliberately does NOT match bare "latest" or bare "today's" —
# legitimate artifacts like the datapack tag [P3.latest_10q_filed] and the
# risk-box label "Today's move:" (the settled 1-day change on the as-of bar)
# must pass untouched. Only discovery-intent phrasings are forbidden.
CURRENT_DATA_PHRASES = (
    "websearch", "(discovery", "current web", "latest catalyst", "latest news",
    "today's news", "today's catalyst", "today's headlines",
    "today’s news", "today’s catalyst", "today’s headlines",
)


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


BULL_SECTION_RE = re.compile(r"##\s*Bull case\b(.*?)(?=\n##\s|\Z)", re.S | re.I)
BEAR_SECTION_RE = re.compile(r"##\s*Bear case\b(.*?)(?=\n##\s|\Z)", re.S | re.I)
QUOTE_RE = re.compile(r'["“]([^"“”]{8,200})["”]')
BULL_CUE_RE = re.compile(r"\bbull'?s?\b", re.I)


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def check_debate_fidelity(debate_text):
    """Invariant-1 fix: any quoted/attributed "bull" claim inside the Bear
    case section must appear verbatim inside the Bull case section — a
    quote near a bull-attribution cue that the bull never made is a
    fabricated strawman, not a rebuttal. Returns list of (ok, message);
    empty if the text has no Bull/Bear case sections (nothing to check)."""
    bull_m = BULL_SECTION_RE.search(debate_text)
    bear_m = BEAR_SECTION_RE.search(debate_text)
    if not bull_m or not bear_m:
        return []
    bull_norm = _norm(bull_m.group(1))
    results = []
    for m in QUOTE_RE.finditer(bear_m.group(1)):
        quote = m.group(1)
        window_start = max(0, m.start() - 100)
        cue_window = bear_m.group(1)[window_start:m.start()]
        if not BULL_CUE_RE.search(cue_window):
            continue  # quoted text not attributed to the bull — not this rule's concern
        if _norm(quote) in bull_norm:
            results.append((True, f"PASS bull-quote-fidelity: bear quote {quote!r} "
                                   f"found verbatim in bull case"))
        else:
            results.append((False, f"FAIL bull-quote-fidelity: bear attributes "
                                   f"{quote!r} to the bull but it does not appear "
                                   f"in the Bull case section — fabricated strawman"))
    return results


GAP_CUE_RE = re.compile(r"\bDATA GAP\b|\bMISSING\b|not (?:currently )?(?:available|present)\b", re.I)
BARE_FACT_RE = re.compile(r"\b([PH]\d+\.[A-Za-z0-9_]+)\b")
GAP_PROXIMITY_CHARS = 40  # tight window: catches "DATA GAP: X [tag]" / "[tag] is MISSING",
# not an unrelated tag mentioned later in the same long analyst paragraph


def check_data_gap_hallucination(text, pack):
    """A "DATA GAP"/MISSING/"not available" cue with a [P#.fact] (or bare
    P#.fact) tag in close proximity (same clause, not just same paragraph)
    whose value IS present (non-null) in the pack is a hallucination — the
    analyst declared a gap for a fact it was actually handed. Returns list
    of (ok, message); only FAILs are appended (no PASS noise)."""
    results = []
    for lineno, line in enumerate(text.splitlines(), 1):
        flagged = set()
        for cue in GAP_CUE_RE.finditer(line):
            lo = max(0, cue.start() - GAP_PROXIMITY_CHARS)
            hi = min(len(line), cue.end() + GAP_PROXIMITY_CHARS)
            flagged.update(BARE_FACT_RE.findall(line[lo:hi]))
        for fact_id in sorted(flagged):
            fact = pack.get(fact_id)
            if not isinstance(fact, dict):
                continue  # not in pack: a real gap, not a hallucination
            if fact.get("v") is None:
                continue  # in pack but null: a real gap
            results.append((False, f"FAIL data-gap-hallucination line {lineno}: "
                                   f"claims {fact_id} is a DATA GAP/MISSING but the "
                                   f"pack has v={fact['v']!r} for it"))
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
# The options block's context TABLES are untagged by design (a number tagged to a
# P8 list fact hard-fails check_pairs). scan_untagged skips the whole block; but
# check_pairs still verifies every tagged P8 SCALAR (the block is NOT stripped
# from check_pairs, unlike the risk box). The "Dealer Positioning" heading also
# matches the "position" CHECK_SECTION keyword, so without this exempt every
# context-table cell would warn.
OPTIONS_BLOCK = ("options-block: inserted verbatim",
                 lambda ln: "options-block: end" in ln)


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
    exempt = _block_line_indices(lines, [RATING_BLOCK, RISKBOX_BLOCK, OPTIONS_BLOCK])
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


def scan_replay_report(text, pack):
    """REPLAY REPORT-TEXT SCAN (--replay only, in addition to
    replay.check_pack_cutoff): hard-fail on report URLs the datapack cannot
    account for, and on current-data phrasing that would leak live/web-search
    content into a historical replay report. Returns list of error strings."""
    errors = []
    pack_json = json.dumps(pack, ensure_ascii=False)
    for m in REPORT_URL_RE.finditer(text):
        url = m.group().rstrip(".,;:)]}>'\"")
        if url not in pack_json:
            errors.append(f"report cites URL not found in datapack: {url}")
    lowered = text.lower()
    for phrase in CURRENT_DATA_PHRASES:
        if phrase in lowered:
            errors.append(f"report contains current-data phrase: {phrase!r}")
    return errors


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    replay_mode = "--replay" in argv
    argv = [a for a in argv if a != "--replay"]
    asof_cutoff = None
    if "--asof-cutoff" in argv:
        i = argv.index("--asof-cutoff")
        asof_cutoff = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    debate_path = None
    if "--debate" in argv:
        i = argv.index("--debate")
        debate_path = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    brief_paths = []
    while "--brief" in argv:
        i = argv.index("--brief")
        if i + 1 < len(argv):
            brief_paths.append(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 2:
        sys.stderr.write(
            "usage: qa_check.py <report.md> <datapack.json> [position.json] [--strict] "
            "[--replay --asof-cutoff YYYY-MM-DD] [--debate 30-debate.md] "
            "[--brief path ...]\n")
        return 2
    if replay_mode and not asof_cutoff:
        sys.stderr.write("qa_check.py: --replay requires --asof-cutoff YYYY-MM-DD\n")
        return 2
    with open(argv[0]) as f:
        report = f.read()
    report = report.replace("−", "-")  # typographic minus (−) → ASCII so negatives parse
    with open(argv[1]) as f:
        datapack = json.load(f)
    position_pack = None
    if len(argv) >= 3 and argv[2] and os.path.exists(argv[2]):
        with open(argv[2]) as f:
            position_pack = json.load(f)
    pack = {**datapack, **position_pack} if position_pack else dict(datapack)
    # An absent position file is normal (flat / back-dated / auth-fail runs write none):
    # skip the merge, never crash — a stray [H#] tag then fails as "no pack entry".

    invariant19_errors = []
    rating = _render_report_mod._rating_from_md(report)
    try:
        level_set = _render_report_mod.parse_levels_marker(report, pack, rating)
        if level_set:
            levels_schema.validate_level_set(level_set, pack, rating)
    except levels_schema.LevelValidationError as e:
        invariant19_errors.append(f"INVARIANT-19 {e}")

    replay_errors = []
    if replay_mode:
        try:
            r_errors, _r_warnings = replay.check_pack_cutoff(
                datapack, asof_cutoff, replay=True, position_pack=position_pack)
        except replay.CutoffError as e:
            sys.stderr.write(f"qa_check.py: invalid --asof-cutoff: {e}\n")
            return 2
        replay_errors += [f"REPLAY {e}" for e in r_errors]
        replay_errors += [f"REPLAY {e}" for e in scan_replay_report(report, pack)]

    debate_errors = []
    if debate_path and os.path.exists(debate_path):
        with open(debate_path) as f:
            debate_results = check_debate_fidelity(f.read())
        debate_errors = [msg for ok, msg in debate_results if not ok]

    gap_errors = []
    for brief_path in brief_paths:
        if not os.path.exists(brief_path):
            continue
        with open(brief_path) as f:
            brief_text = f.read()
        gap_errors += [f"{os.path.basename(brief_path)}: {msg}"
                        for ok, msg in check_data_gap_hallucination(brief_text, pack) if not ok]
    gap_errors += [msg for ok, msg in check_data_gap_hallucination(report, pack) if not ok]

    results = check_pairs(strip_riskbox(report), pack)
    dresults, dwarnings = recompute_derived(pack)
    results += dresults
    warnings = scan_untagged(report) + dwarnings
    result_fails = [msg for ok, msg in results if not ok]
    hard_base = result_fails + warnings if strict else result_fails
    hard = replay_errors + invariant19_errors + debate_errors + gap_errors + hard_base
    # cutoff/replay/invariant-19/debate-fidelity/gap-hallucination failures are
    # always hard, independent of --strict
    out = ["== QA CITE CHECK =="]
    if replay_errors:
        out.append("== REPLAY GUARD ==")
        out += ["! " + msg for msg in replay_errors]
    if invariant19_errors:
        out.append("== INVARIANT 19 (counter-trend triggers) ==")
        out += ["! " + msg for msg in invariant19_errors]
    if debate_errors:
        out.append("== DEBATE FIDELITY (bear quoting bull) ==")
        out += ["! " + msg for msg in debate_errors]
    if gap_errors:
        out.append("== DATA GAP HALLUCINATION ==")
        out += ["! " + msg for msg in gap_errors]
    out += [("  " if ok else "! ") + msg for ok, msg in results]
    if warnings:
        out.append("== WARNINGS (non-fatal) ==")
        out += ["  " + w for w in warnings]
    out.append(f"== {len(results) - len(hard_base)} pass, {len(hard)} fail, "
               f"{len(warnings)} warn ==")
    sys.stdout.write("\n".join(out) + "\n")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
