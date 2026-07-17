# Findings — v2-evolve Phase 0

- ERD's Phase 0 defects reference code that exists ONLY on
  `worktree-left-side-signals` (unmerged buy-the-dip PR #1), not `master`.
  Branch point corrected accordingly (see task_plan.md).
- Item 3 (judges miscounting exhaustion conditions) reproduces in
  `runs/SOXL-2026-07-17-0223/50-votes/*.md` (left-side-signals worktree,
  gitignored) even though the run postdates the 1af21ed prompt fix — the 4
  conditions are correctly *defined* in prose but never precomputed as
  booleans, so judges hand-count and miscall "ATR stretch" as one of them.
  Confirms item 3 is a real, currently-live defect, not already-fixed.
- No `run-stats` script exists anywhere in `scripts/` — item 6's footer
  fields have zero computing mechanism despite SKILL.md invariant 7 CLAIMING
  "run-stats collection + template slot" as the enforcement. The claim in
  SKILL.md is aspirational, not implemented.
- Item 8 (3-way price cross-check) has no existing 3rd source wired in;
  only `schwab_quote.py` + `tiingo_oracle.py` feed Invariant 10. Highest-risk
  item — may need to fall back to "disclosed discrepancy fact" instead of
  full 2-of-3 resolution if a low-risk 3rd source isn't available.
