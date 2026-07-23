# Project instructions — trading-research (v2) + quant-engine (v3)

## Orchestration
- **Use ultracode (multi-agent Workflow orchestration) for substantive multi-step work** — scale
  agent count to stakes. Default to it for phase-structured builds, audits, and reviews on this
  effort. Route by role (Opus orchestrates + final gate; Codex/Cursor for independent judgment/
  review; Sonnet for research/execution; Haiku for chores). Gated actions (git push, global
  config, secrets) still need their own approval.

## Ground truth
- v2 = this repo (the `trading-research` skill). v3 = `~/.claude/skills/quant-engine` (vectorbt
  honesty-gate backtester). Durable docs/handoffs live in the VAULT, not in either repo.
- A feature is "shipped" ONLY after a LIVE end-to-end run against real dependencies — mocked/
  unit-green/doc-review is never "done".
