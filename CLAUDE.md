# Project instructions — skill (trading-research) + quant-engine

## Orchestration
- **Use ultracode (multi-agent Workflow orchestration) for substantive multi-step work** — scale
  agent count to stakes. Default to it for phase-structured builds, audits, and reviews on this
  effort. Route by role (Opus orchestrates + final gate; Codex/Cursor for independent judgment/
  review; Sonnet for research/execution; Haiku for chores). Gated actions (git push, global
  config, secrets) still need their own approval.

## Ground truth
- **Naming (canonical):** the two programs are **`skill`** = this repo (the `trading-research`
  skill) and **`quant-engine`** = `~/.claude/skills/quant-engine` (vectorbt honesty-gate
  backtester). Do NOT call them "v2"/"v3" — those tokens are reserved for real semantic
  versioning of each program. Vault feature folders live under `tradingagents/skill/` and
  `tradingagents/quant-engine/`. Durable docs/handoffs live in the VAULT, not in either repo.
- A feature is "shipped" ONLY after a LIVE end-to-end run against real dependencies — mocked/
  unit-green/doc-review is never "done".

## Tracking — where an open item goes
- **Two homes, one rule.** Bugs, optimizations, small well-scoped fixes, feedback, and feature
  *seeds* → `tradingagents/backlog.md` (vault; churny, delete-when-done). A feature or refactor
  that has earned its own impl-plan → `tradingagents/feature-registry.md` (a `proposed` row,
  Stop-hook enforced). A backlog item GRADUATES to a registry row only when it earns an impl-plan.
- **Never double-track** — one home per item. Never `mv`/rename `feature-registry.md` (keyed by
  `enforce-feature-registry.py` + wikilinks); retitle the H1 only.
- **Registry row format (ALWAYS, every row):** `| # | Codename | Capability | Notes | Prog | Status | Ref |`.
  **#** = a stable `F<n>` id (next free integer, never reused/renumbered, kept across status moves).
  **Codename** = a unique 1–2-word `**bold**` handle. **Capability** = exactly ONE sentence.
  **Notes** = bulleted progress/caveats (`• …<br>• …`, or `—`). Fill all four on any new row.
- **Priorities: P0/P1/P2/P3.** P0 = urgent / blocker · P1 = important / high ROI · P2 = normal ·
  P3 = low / someday. Keep `backlog.md` **sorted and grouped by priority section**. Mark an item done
  by DELETING its row (git holds the history), not by annotating it.
