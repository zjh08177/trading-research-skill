# trading-research (Cursor host)

Run the trading-research pipeline from this Cursor session.

**Install:** copy or symlink this file to `~/.cursor/commands/trading-research.md`.

You are the pipeline **orchestrator**, host = **cursor**.

1. Read the skill contract by absolute path (the skill lives outside the opened
   workspace): `cat ~/.claude/skills/trading-research/SKILL.md`.
2. Export `TRADING_RESEARCH_HOST=cursor`. After `00-scope.md` exists, run the
   mandatory absolute usage start command and `eval` its export:
   `/Users/bytedance/.claude/skills/trading-research/.venv/bin/python /Users/bytedance/.claude/skills/trading-research/scripts/usage.py start --mode report --ticker <TICKER> --job-tier <J#> --asset-class equity --run-id <RUN_ID> --run-dir <RUN_DIR>`.
   End with the matching absolute `usage.py end` or `usage.py fail`.
3. Follow it end to end, applying the **## Host runtimes** section: every Agent
   stage becomes a `cursor-agent -p` call with the pinned per-tier slots; judges
   run plan-mode against temp files and are collected into `50-votes/`; the HTML
   deliverable degrades to a local file (no Artifact tool on Cursor).
4. Single-ticker only — batch, the daily monitor, and crypto stay Claude-Code-only.
5. **Historical as-of replay** (`<TICKER> <YYYY-MM-DD>` or `<YYYY/MM/DD>` past
   date): follow SKILL.md's **## Historical as-of replay** section. No
   `15-position.json` position artifact is produced or read. No WebSearch
   evidence is gathered by any agent stage — the sentiment/analyst role must
   rely on Marketaux/pack data only and mark `DATA GAP` when it is empty.
   Publish to `reports/replay/<TICKER>/`, never `reports/single-ticker/`.
   Single-ticker only, same as the rest of this host.

This entry is a pointer: all pipeline logic lives in SKILL.md, not here.
