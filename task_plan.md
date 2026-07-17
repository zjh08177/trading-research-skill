# Task Plan — v2-evolve Phase 0 (ship the 9 confirmed defect fixes)

## Goal
Implement all 9 Phase 0 items from [[erd-v2-evolve]] (ERD path:
`Projects/personal/tradingagents/v2-skillify/v2-evolve/erd-v2-evolve.md` in
the vault) on branch `v2-evolve-phase0`, then run the Phase 0 exit gate
(item 9): re-run the pipeline and score how many of the original 9
proponent-found defects still reproduce.

## Repo / branch setup (done)
- New git worktree: `/Users/bytedance/Work/Damn/trading-research-skill/.claude/worktrees/v2-evolve-phase0`
- Branch `v2-evolve-phase0`, based on `worktree-left-side-signals` HEAD
  (8f6ec03), NOT master — the buy-the-dip feature code (Mean-Reversion
  analyst, stretch.py, P9 facts, counter-trend prompts) that Phase 0's
  defects reference only exists on that unmerged branch. Kept as a
  separate branch (not commits piled onto left-side-signals) per the
  handoff's explicit "do not conflate the two threads" instruction.
- Main checkout (`/Users/bytedance/Work/Damn/trading-research-skill`) has
  pre-existing unrelated dirty state (`.gitignore` diff adding `docs/`
  ignore, stray `.DS_Store`/`cmp-*.png`/`smoke.err`) — NOT mine, left
  untouched, not part of this work.

## Live-run baseline (exit-gate ground truth)
`runs/SOXL-2026-07-17-0223/` in the **left-side-signals worktree**
filesystem (gitignored, not in git — must read it there, not in the new
v2-evolve-phase0 worktree). This run happened AFTER commit 1af21ed
(counter-trend prompt fix), confirming item 3 is still live: vote-1/2/3 in
`50-votes/` still count "ATR stretch" as 1 of the "4 exhaustion-turning
conditions" even though current prompts.md never lists stretch as one of
the 4 — judges are hand-counting prose, getting it wrong. This is my
primary re-verification anchor for item 3 and the item-9 exit gate.

## Phase 0 items — status and file:line (current branch, verified by direct read)

| # | Defect | Verified still live? | Files to touch | Status |
|---|---|---|---|---|
| 1 | Bear card's Inputs line doesn't include bull output despite "Attack the bull's weakest tagged claims" mission | YES — `prompts.md:118-121` (Bear Inputs = `DATA PACK + analyst briefs`, same as Bull's `prompts.md:108-113`) | `references/prompts.md` (bear card), `scripts/qa_check.py` (new verbatim-quote rule) | pending |
| 2 | Report writer's "Sell → trim ~25-40%" sizing band is an invented constant | YES — `prompts.md:284-286` | `references/prompts.md` (position framing card) — relabel as house convention (cheapest correct fix; deriving from risk_box.py is a bigger lift and ERD offers this as the alternative) | pending |
| 3 | Judges hand-count "4 exhaustion-turning conditions" and get it wrong (miscount "ATR stretch" as one) | YES — reproduced in a run AFTER the 1af21ed fix; `prompts.md:152-162` (judge card), `prompts.md:204-214` (condition definitions) | new Stage 1c script logic (extend `scripts/stretch.py` or new `scripts/exhaustion.py`) emitting `P9.exhaustion_rsi_turn`/`_vol_decay`/`_higher_closes`/`_crashfree_window` booleans + `P9.exhaustion_tally` ("k/4" string); `scripts/render_meanrev.py` (render into pack/report); `references/prompts.md` (judge + writer cards cite tally, state "stretch is precondition not condition") | pending |
| 4 | Fundamental analyst can hallucinate "DATA GAP" for a fact actually present in the pack | Plausible per ERD (not independently re-run this session) | `scripts/qa_check.py` — new rule: any `DATA GAP`/`MISSING`/`not present` claim naming a `[P#.fact]` id that exists in the pack is a hard fail | pending |
| 5 | Stage 7 sonnet prose-QA pass has no persisted artifact — cannot prove it ran | YES — `SKILL.md:41` writes only `70-qa.txt` (qa_check.py mechanical output); prose card at `prompts.md:296-308` has no "write to file" instruction | `SKILL.md` (Stage 7 row + pipeline step text), `references/prompts.md` (QA prose card: mandate writing `70-qa-prose.txt`) | pending |
| 6 | Invariant 7 footer fields (agent count, model mix, wall-clock, token cost) are prose asks with NO computing mechanism — writer guesses | YES — confirmed no `run-stats` script exists anywhere in `scripts/`; `report-template.md:119-124` has bare `{{agent_count}}`/`{{model_mix}}`/`{{wall_s}}`/`{{cost_usd}}` placeholders with nothing filling them | new `scripts/run_stats.py` (tally agents/models/wall-clock/tokens from run-folder artifact timestamps + a session-recorded stage log), `qa_check.py` (hard-fail if any of the 5 footer fields missing/placeholder), `SKILL.md` (Stage 8 / orchestrator instruction to call it) | pending |
| 7 | Stage 1c under-distills `11-history.json` — additional cluster/shape facts needed | Subsumed by item 3's 4 booleans per ERD text; no additional file:line given beyond that | covered by item 3's implementation; note in commit, no separate script | merge into #3 |
| 8 | `CROSS-CHECK FAIL` (schwab vs tiingo P1 price mismatch) survives unresolved to judges | Not independently reproduced this session (would need a live disagreement) — mechanism gap confirmed by code read: only 2 sources (`schwab_quote.py` + `tiingo_oracle.py`) feed the Invariant-10 cross-check, no 3rd/resolution logic exists | investigate a 3rd allow-listed source (candidate: existing `stock-market-pro` fallback path already referenced in SKILL.md's P1 row) before building new vendor CLI; if no low-risk 3rd source exists within budget, document as a disclosed discrepancy fact per Phase 1's constraint instead of blocking | pending — highest uncertainty item |
| 9 | Exit gate: re-run pipeline, score how many of the original 9 (proponent-found, not this Phase-0 table's) defects still reproduce | — | live run(s) after 1-8 land | pending, LAST |

## Order of execution
1 → 2 → 4 → 5 → 6 → 3 → 8 → 9. (Cheapest/most self-contained first; 3 and 8
are the two real engineering items; 9 is the live verification gate.)
Each item = one atomic commit on `v2-evolve-phase0`.

## Done condition (per loop-goal contract)
End state: all 9 items land as atomic commits; a **live** `/trading-research`
run (real vendor APIs, real ticker) is executed post-fix and its artifacts
prove: (a) qa_check.py passes with the new rules active, (b) the judge
votes/report show correct exhaustion-condition tallies sourced from the new
P9 facts (not hand-counted), (c) the disclosure footer is fully populated
by run_stats.py, (d) bear card's Inputs demonstrably include bull's wave-1
output. Item 9's score (defects still reproducing / 9) is reported, pasted
from the actual run artifacts — not asserted.

## Scope lock
Only modify: `references/prompts.md`, `SKILL.md`, `references/report-template.md`,
`scripts/qa_check.py`, `scripts/render_meanrev.py`, `scripts/stretch.py` (or a
new `scripts/exhaustion.py`), a new `scripts/run_stats.py`, and (if item 8's
3rd source is added) a new/extended vendor CLI under `scripts/vendors/`.
Never touch `runs/` (gitignored, live account/report data), never touch the
`worktree-left-side-signals` branch/worktree directly (buy-the-dip PR #1,
unrelated, still open).

## Stop condition
If a live pipeline run fails to complete (vendor auth/creds failure, etc.)
after 2 attempts with different tickers: stop, report the exact blocker,
and treat item 9 as incomplete rather than fabricating a pass.

## Errors Encountered
(none yet)
