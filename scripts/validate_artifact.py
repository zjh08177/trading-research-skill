#!/usr/bin/env python3
"""Deterministic accept-gate for a single worker artifact. Stdlib only.

A worker (analyst / debater / risk officer / judge / writer) can silently emit
its brief TWICE, or emit two different briefs glued at a seam, or truncate. The
audited run UNH-2026-07-21-2147 shipped both defects: `20-analyst-sent.md`
concatenated two sentiment briefs at `...next earnings catalyst.Tape: The July
17...`, reached `45-judge-bundle.md:175`, and the judges voted on it
UNDISCLOSED; `30-debate-bear-malformed.md` triplicated the bear case and echoed
the bull, and was caught only by a human eyeball. This module turns that
detection into a gate: `check()` runs BEFORE any downstream stage reads the
artifact.

API:  check(role, text) -> (ok: bool, reasons: list[str])
CLI:  validate_artifact.py <role> <file>   # exit 0 pass, 1 fail, 2 bad usage

`role` accepts either a bare role name (`sent`, `bull`, `vote`, ...) or an
artifact FILENAME (`20-analyst-sent.md`, `50-votes/vote-2.md`), which is mapped
to its role.

Three check families, each emitting its own MALFORMED(...) reason class:

1. MALFORMED(duplication) - the artifact contains more than one emission.
   D1 a normalized paragraph >=120 chars appears >=2x
   D2 >30% of 12-word shingle instances belong to a repeated shingle
   D3 the terminal `KEY POINTS` marker appears >=2x (single-emission roles)
   D4 a concatenation seam - `<lower>.<Upper>` with no space, minus a known
      abbreviation allowlist
   D1/D2 catch verbatim re-emission; D3/D4 catch a RE-WORDED second emission,
   which D1/D2 cannot see (the sent defect scored dup_para=0, shingles=2.9%).

2. MALFORMED(shape) - the artifact does not match its role contract.

3. MALFORMED(truncation) - the artifact is shorter than any real one.

Thresholds were calibrated against all 2220 role artifacts under `runs/`, not
guessed; see THRESHOLD NOTES below each constant.

Known limitations, both deliberate:

* The bull/bear shape check assumes ONE role per artifact - the contract
  `pipeline_driver.py` writes. A legacy COMBINED `30-debate.md` holding both
  cases (and, in multi-round runs, several `KEY POINTS` markers) is not a valid
  input to this gate; pass the per-role artifacts instead.
* The bull/bear heading and the risk lead-with-block rules encode the CURRENT
  contract (SKILL.md pipeline rows 3a/3b/4b, references/prompts.md), which most
  archived runs predate: 96% of corpus bull/bear artifacts carry no heading and
  78% of corpus risk artifacts do not lead with the block. Those are legacy
  non-conformance, not false positives -- but it means the bull/bear/risk
  worker prompts MUST request these shapes or the gate will quarantine every
  debate and risk stage. Callers gating legacy artifacts want
  `duplication_reasons()`, which is contract-era-independent.
"""
import re
import sys
from collections import Counter

# --- roles -----------------------------------------------------------------

BRIEF_ROLES = ("fund", "tech", "sent", "meanrev")
ROLES = BRIEF_ROLES + ("bull", "bear", "risk", "vote", "report")
# Roles whose artifact is exactly ONE worker response. `report` is excluded: it
# is a compilation that legitimately embeds verbatim blocks from other stages.
SINGLE_EMISSION_ROLES = tuple(r for r in ROLES if r != "report")

# Leading `BACKEND:/MODEL:/ROLE:/SLOT:/RECEIPT:` provenance lines the driver
# prepends (SKILL.md:490; consumed by ensemble.py). Stripped before every check
# so provenance never counts as content -- but only the LEADING run: a header
# block reappearing mid-body is itself concatenation evidence.
HEADER_RE = re.compile(r"^(BACKEND|MODEL|ROLE|SLOT|RECEIPT):\s")

# --- duplication thresholds ------------------------------------------------

# D1. THRESHOLD NOTES: 120 chars is ~1 sentence; shorter repeats are legitimate
# (`DATA GAP: next earnings date is not announced [P5.next_earnings].` is 63
# chars and repeats honestly). Fires on 49/2220 corpus artifacts, all of which
# independently trip D2 or D3.
MIN_DUP_PARAGRAPH_CHARS = 120

# D2. THRESHOLD NOTES: 12 words is long enough that shared boilerplate
# (`[P9.base_rate_ci_note]` caveats, risk-box phrasing) does not collide.
# Legitimate artifacts score 0.0-10.4%; the duplicated ones score 68-93%. The
# 30% line sits in an empty band, so it is not sensitive to small changes.
SHINGLE_WORDS = 12
MAX_REPEATED_SHINGLE_FRAC = 0.30

# D3. THRESHOLD NOTES: every role card ends with a single `KEY POINTS:` line
# (references/prompts.md:20). ABSENCE is NOT flagged -- 367/705 corpus analyst
# briefs predate the marker and were published legitimately -- but a SECOND
# marker means a second emission. Fires on 38 corpus artifacts that trip
# nothing else; every one inspected was a genuine double-emission (e.g.
# SOXL-2026-07-09-1306/20-analyst-fund.md is two unrelated briefs stacked).
TERMINAL_MARKER = "KEY POINTS"
MAX_TERMINAL_MARKERS = 1

# D4. THRESHOLD NOTES: the literal signature of two responses glued together
# (`...catalyst.Tape: The July 17...`). Requiring a lowercase/`)` before the
# period excludes fact tags (`P1.last`) and initialisms (`U.S.News`). Across
# 2220 corpus artifacts it fires 38 times and NEVER on prose that is not a
# seam; `grep -roE '\b(vs|e\.g|i\.e|Inc|No|etc)\.[A-Z][A-Za-z]' runs` returns
# zero, so the allowlist below is belt-and-braces for future artifacts.
SEAM_RE = re.compile(r"([A-Za-z]{0,8})([a-z\)])([.!?])([A-Z][A-Za-z])")
SEAM_ABBREVIATIONS = {
    "vs", "eg", "ie", "no", "fig", "approx", "etc", "inc", "corp", "ltd",
    "co", "dr", "mr", "mrs", "ms", "st", "jr", "sr", "cf", "al", "est",
}

# --- length floors ---------------------------------------------------------

# THRESHOLD NOTES: truncation detection, not style policing. Each floor sits
# well below the smallest legitimate corpus artifact of that role, so a short
# but complete brief is never blocked:
#   brief  min observed 381   -> floor 250
#   bull   min observed 1155  -> floor 400
#   bear   min observed 1232  -> floor 400
#   risk   min observed 1092  -> floor 400 (+150 of narration below the block)
#   vote   min observed 324   -> floor 120 (a 4-field VERDICT line alone is ~200)
#   report min observed 9542  -> floor 3000
MIN_BODY_CHARS = {
    "fund": 250, "tech": 250, "sent": 250, "meanrev": 250,
    "bull": 400, "bear": 400, "risk": 400, "vote": 120, "report": 3000,
}
MIN_RISK_NARRATION_CHARS = 150

# --- shape patterns --------------------------------------------------------

# Mirrors ensemble.py:VERDICT_RE. ensemble.py is the authority on the vote
# contract; keep the two in sync. A vote this gate accepts must be a vote
# ensemble.py can tally, or the tally silently drops a judge.
VERDICT_RE = re.compile(
    r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)\s*\|\s*"
    r"CONVICTION:\s*(\d+)\s*\|\s*ENTRY-PATH:\s*(.+?)\s*\|\s*WHY:\s*(.+?)\s*$")
# Deliberately mirrors qa_check.py:BULL_SECTION_RE/BEAR_SECTION_RE rather than
# accepting any Bull/Bear-ish heading. qa_check.py's invariant-1 fabricated-
# strawman detector (`--debate`) returns [] -- a SILENT no-op -- when it cannot
# find these exact headings, so an artifact whose heading this gate accepts but
# qa_check.py cannot parse would disable an honesty gate without saying so.
# 96% of the historical corpus is heading-less, i.e. ran with invariant 1
# silently off; that is the defect, not the reason to relax this.
SIDE_HEADING_RE = {
    "bull": re.compile(r"^##\s*Bull case\b", re.M | re.I),
    "bear": re.compile(r"^##\s*Bear case\b", re.M | re.I),
}
RISKBOX_OPEN = "<!-- riskbox-block: inserted verbatim, do not edit -->"
RISKBOX_END = "<!-- riskbox-block: end -->"
# 0/173 corpus reports are missing any of these; a report lacking one is
# structurally incomplete, not merely unusual.
REQUIRED_REPORT_SECTIONS = (
    "## Executive summary", "## Thesis", "## Risk box",
    "## Data gaps", "## Disclosure",
)


class UnknownRole(ValueError):
    """Raised when the role argument names no known pipeline role."""


def resolve_role(role):
    """Map a role name OR an artifact filename onto a canonical role.

    The pipeline's own file names are the natural handle at a call site
    (`validate_artifact.py "$(basename $f)" "$f"`), so accept both forms.
    """
    raw = (role or "").strip()
    if raw in ROLES:
        return raw
    key = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".md", ".txt"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    if key.endswith("-raw"):
        key = key[: -len("-raw")]
    if key in ROLES:
        return key
    if "vote" in key:
        return "vote"
    if "bear" in key:
        return "bear"
    if "bull" in key:
        return "bull"
    if key.startswith("20-analyst-"):
        name = key[len("20-analyst-"):]
        if name in BRIEF_ROLES:
            return name
        raise UnknownRole(
            f"unknown analyst role {name!r} in {raw!r}; "
            f"known analyst roles: {', '.join(BRIEF_ROLES)}")
    if key.startswith("40-risk"):
        return "risk"
    if key.startswith("60-report"):
        return "report"
    if key.startswith("30-debate"):
        raise UnknownRole(
            f"{raw!r} names a combined debate artifact, which holds two roles; "
            "pass an explicit role ('bull' or 'bear') and the per-role file")
    raise UnknownRole(f"unknown role {raw!r}; known roles: {', '.join(ROLES)}")


def strip_worker_headers(text):
    """Drop the LEADING run of provenance headers and blank lines."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip() or HEADER_RE.match(lines[i]):
            i += 1
            continue
        break
    return "\n".join(lines[i:])


def _normalize(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def _paragraphs(body):
    return [p for p in (_normalize(x) for x in re.split(r"\n\s*\n", body)) if p]


def _shingles(body, k=SHINGLE_WORDS):
    words = _normalize(body).split()
    return [" ".join(words[i:i + k]) for i in range(len(words) - k + 1)]


def _seams(body):
    """Return concatenation seams, minus the abbreviation allowlist."""
    found = []
    for prefix, last, punct, tail in SEAM_RE.findall(body):
        word = (prefix + last).lower()
        if word in SEAM_ABBREVIATIONS or word.lstrip(".").rstrip(".") in SEAM_ABBREVIATIONS:
            continue
        found.append(f"{last}{punct}{tail}")
    return found


def duplication_reasons(role, text):
    """Duplication reasons only. Public so `qa_check.py --brief` can wire this
    single family in as a hard fail without adopting the shape/length rules
    (defense in depth for hosts that do not run pipeline_driver.py)."""
    role = resolve_role(role)
    body = strip_worker_headers(text)
    reasons = []

    counts = Counter(p for p in _paragraphs(body) if len(p) >= MIN_DUP_PARAGRAPH_CHARS)
    repeated = [(p, n) for p, n in counts.items() if n > 1]
    if repeated:
        para, n = max(repeated, key=lambda kv: kv[1])
        reasons.append(
            f"MALFORMED(duplication): {len(repeated)} paragraph(s) of "
            f">={MIN_DUP_PARAGRAPH_CHARS} chars repeat; worst appears {n}x: "
            f"{para[:90]!r}...")

    shingles = _shingles(body)
    if shingles:
        sc = Counter(shingles)
        frac = sum(n for n in sc.values() if n > 1) / len(shingles)
        if frac > MAX_REPEATED_SHINGLE_FRAC:
            reasons.append(
                f"MALFORMED(duplication): {frac:.0%} of {SHINGLE_WORDS}-word "
                f"shingles are repeated (limit {MAX_REPEATED_SHINGLE_FRAC:.0%}) "
                "-- the body is emitted more than once")

    if role in SINGLE_EMISSION_ROLES:
        markers = body.count(TERMINAL_MARKER)
        if markers > MAX_TERMINAL_MARKERS:
            reasons.append(
                f"MALFORMED(duplication): {markers} '{TERMINAL_MARKER}' markers "
                f"(expected at most {MAX_TERMINAL_MARKERS}) -- a single-role "
                "artifact ends once, so this is a second emission")

    seams = _seams(body)
    if seams:
        reasons.append(
            f"MALFORMED(duplication): {len(seams)} concatenation seam(s) "
            f"{sorted(set(seams))[:4]} -- sentence-end glued to a capitalized "
            "restart with no space, the signature of two responses joined")
    return reasons


def shape_reasons(role, text):
    """Role-shape reasons only."""
    role = resolve_role(role)
    body = strip_worker_headers(text)
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
    reasons = []

    if role == "vote":
        # Same contract ensemble.py:parse_vote enforces: exactly one VERDICT
        # line, and it is the last non-blank line.
        verdicts = [ln for ln in lines if ln.strip().startswith("VERDICT:")]
        if len(verdicts) != 1:
            reasons.append(
                f"MALFORMED(shape): vote has {len(verdicts)} 'VERDICT:' lines, "
                "expected exactly 1 (ensemble.py drops the file otherwise)")
        elif not lines or lines[-1].strip() != verdicts[0].strip():
            reasons.append(
                "MALFORMED(shape): the 'VERDICT:' line is not the last line of "
                f"the vote (last line is {lines[-1].strip()[:60]!r})")
        elif not VERDICT_RE.match(verdicts[0].strip()):
            reasons.append(
                "MALFORMED(shape): vote line is not the 4-field "
                "'VERDICT: <rating> | CONVICTION: <n> | ENTRY-PATH: <p> | "
                f"WHY: <w>' form: {verdicts[0].strip()[:90]!r}")

    elif role in SIDE_HEADING_RE:
        other = "bear" if role == "bull" else "bull"
        if not SIDE_HEADING_RE[role].search(body):
            reasons.append(
                f"MALFORMED(shape): no '## {role.capitalize()} case' heading")
        if SIDE_HEADING_RE[other].search(body):
            reasons.append(
                f"MALFORMED(shape): a {role} artifact contains a "
                f"'## {other.capitalize()} case' heading -- it echoed the "
                "other side's brief instead of writing its own")

    elif role == "risk":
        if not body.lstrip().startswith(RISKBOX_OPEN):
            reasons.append(
                "MALFORMED(shape): risk artifact does not LEAD with the "
                f"verbatim risk box ({RISKBOX_OPEN!r}) -- invariant 16 requires "
                "the computed block reproduced unchanged, then narration")
        elif RISKBOX_END not in body:
            reasons.append(
                f"MALFORMED(shape): risk box opened but never closed "
                f"({RISKBOX_END!r} missing)")
        else:
            narration = body.split(RISKBOX_END, 1)[1].strip()
            if len(narration) < MIN_RISK_NARRATION_CHARS:
                reasons.append(
                    f"MALFORMED(truncation): only {len(narration)} chars of "
                    f"narration after the risk box (minimum "
                    f"{MIN_RISK_NARRATION_CHARS}) -- the officer reproduced the "
                    "block but wrote no analysis")

    elif role == "report":
        missing = [s for s in REQUIRED_REPORT_SECTIONS
                   if not re.search("^" + re.escape(s), body, re.M)]
        if missing:
            reasons.append(
                f"MALFORMED(shape): report is missing required section(s): "
                f"{', '.join(missing)}")
    return reasons


def length_reasons(role, text):
    """Non-emptiness / truncation reasons only."""
    role = resolve_role(role)
    body = strip_worker_headers(text).strip()
    if not body:
        return [f"MALFORMED(truncation): {role} artifact is empty "
                "(no content after the provenance headers)"]
    floor = MIN_BODY_CHARS[role]
    if len(body) < floor:
        return [f"MALFORMED(truncation): {role} artifact is {len(body)} chars, "
                f"below the {floor}-char floor for this role"]
    return []


def check(role, text):
    """Return (ok, reasons) for one artifact. `role` may be a role name or an
    artifact filename. Raises UnknownRole if the role cannot be resolved --
    callers must not treat an unresolvable role as a pass."""
    role = resolve_role(role)
    reasons = (length_reasons(role, text)
               + duplication_reasons(role, text)
               + shape_reasons(role, text))
    return (not reasons), reasons


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] in ("-h", "--help"):
        sys.stderr.write(
            "usage: validate_artifact.py <role> <file>\n"
            f"  role: {', '.join(ROLES)} (or an artifact filename)\n"
            "  exit 0 accept, 1 MALFORMED (reasons on stdout), 2 bad usage\n")
        return 0 if argv[:1] in (["-h"], ["--help"]) else 2
    role_arg, path = argv
    try:
        role = resolve_role(role_arg)
    except UnknownRole as exc:
        sys.stderr.write(f"validate_artifact: {exc}\n")
        return 2
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        sys.stderr.write(f"validate_artifact: cannot read {path}: {exc}\n")
        return 2
    ok, reasons = check(role, text)
    if ok:
        sys.stdout.write(f"OK {role} {path}\n")
        return 0
    sys.stdout.write(f"FAIL {role} {path}\n")
    for reason in reasons:
        sys.stdout.write(f"! {reason}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
