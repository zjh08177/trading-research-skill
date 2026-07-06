# trading-research (Cursor host)

Run the trading-research pipeline from this Cursor session.

**Install:** copy or symlink this file to `~/.cursor/commands/trading-research.md`.

You are the pipeline **orchestrator**, host = **cursor**.

1. Read the skill contract by absolute path (the skill lives outside the opened
   workspace): `cat ~/.claude/skills/trading-research/SKILL.md`.
2. Follow it end to end, applying the **## Host runtimes** section: every Agent
   stage becomes a `cursor-agent -p` call with the pinned per-tier slots; judges
   run plan-mode against temp files and are collected into `50-votes/`; the HTML
   deliverable degrades to a local file (no Artifact tool on Cursor).
3. Single-ticker only — batch, the daily monitor, and crypto stay Claude-Code-only.

This entry is a pointer: all pipeline logic lives in SKILL.md, not here.
