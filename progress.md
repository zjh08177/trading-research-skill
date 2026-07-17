# Progress — v2-evolve Phase 0

## Session 1 (2026-07-17)
- Set up isolated worktree + branch `v2-evolve-phase0` off
  `worktree-left-side-signals` HEAD (8f6ec03).
- Verified current file:line for items 1,2,3,4(partial),5,6,8 against the
  actual repo state (ERD's citations were close but line numbers had
  drifted slightly; item 3 re-confirmed live via the actual SOXL run votes).
- Wrote task_plan.md / findings.md / progress.md.
- Items 1,2,4,5,6 committed (fc71196, 9dbbce7, bf9f83f, ccd53da, 25a2d45).
  Each verified against the real SOXL-2026-07-17-0223 run artifacts where
  applicable (bear's fabricated quote, fund analyst's DATA GAP hallucination,
  the "Agents: 3" undercounted footer) — all confirmed caught by the new
  qa_check.py rules before committing. Full test suite green except 2
  pre-existing unrelated failures (test_build_datapack_registry.py, confirmed
  present at the branch base commit too — UW/Schwab vendor sunset, not mine).
- Items 3, 8 committed (f43a496, 245b30b). All 9 Phase 0 items landed as of
  commit 245b30b.
- Item 9 (exit gate) run live: full pipeline on SOXL (fresh live pull,
  2026-07-17 ~13:40-14:07 PT), real vendor APIs (schwab/tiingo/finnhub/
  apewisdom + WebSearch fallback for news), real Agent-tool subagents for
  every LLM stage (4 analysts, bull, bear x2, risk officer, 3 judges,
  writer, prose-QA pass). qa_check.py clean pass both before AND after
  Stage 7c (--check-footer). 60-report.html rendered. Full scoring in
  task_plan.md's Exit Gate Scoring section.
- Found + fixed ONE NEW defect during the live run: qa_check.py's
  --check-footer disclosure check was unconditional, creating a circular
  Stage-7c dependency (qa_check.py could never exit 0 while the writer's
  intentionally-unfilled footer tokens were present, but Stage 7c — which
  fills them — was gated on qa_check.py exiting 0 first). Fixed by gating
  the check behind an explicit --check-footer flag, run only on the
  post-patch pass (commit 33a187e). Exactly the class of bug a live run
  catches that reading the prompts/scripts alone would not.
- Also found + fixed a bear quote-mark-discipline gap: the bear's first
  live attempt correctly avoided fabricating NEW claims (item 1's core
  fix held) but put close paraphrases of the bull's argument in quotation
  marks, which the strict verbatim check_debate_fidelity() flagged as
  false-positive misquotes. Tightened the bear card (quote marks = only
  for character-for-character copies; paraphrase = no quote marks) and
  re-verified clean on a second live attempt — ALL PASS.
- Session complete: all 9 Phase 0 items shipped + live-verified. See
  task_plan.md for the full scoring against the original defect table and
  disclosed test-coverage gaps (item 2 not exercised — no live position;
  item 8's 2-of-3 resolution branch not exercised — schwab/tiingo agreed
  this run; only one ticker re-run, not the ERD's "SOXL + a fresh ticker").
