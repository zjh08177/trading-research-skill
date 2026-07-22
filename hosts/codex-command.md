# trading-research (Codex host)

Run the trading-research pipeline from this Codex session.

**Install:** copy or symlink this file to `~/.codex/prompts/trading-research.md`.

You are the pipeline **orchestrator**, host = **codex**. On this host you do NOT
run the stages yourself — `scripts/pipeline_driver.py` owns Stages 1–7c as one
deterministic process and every LLM worker is a `cursor-delegate.sh` subprocess.

1. Read the skill contract by absolute path (the skill lives outside the opened
   workspace): `cat ~/.claude/skills/trading-research/SKILL.md`, and follow its
   **## Host runtimes → ### Full stage mapping (host = codex)** section.
2. Export `TRADING_RESEARCH_HOST=codex`. After `00-scope.md` exists, run the
   mandatory absolute usage start command and `eval` its export:
   `/Users/bytedance/.claude/skills/trading-research/.venv/bin/python /Users/bytedance/.claude/skills/trading-research/scripts/usage.py start --mode report --ticker <TICKER> --job-tier <J#> --asset-class equity --run-id <RUN_ID> --run-dir <RUN_DIR>`.
   End with the matching absolute `usage.py end` or `usage.py fail`.
3. Four steps, nothing else: **(0)** write `00-scope.md` + `routing.json` into the
   run dir `<SKILL_DIR>/runs/<TICKER>-<asof>-<hhmm>`; **(1)** launch
   `pipeline_driver.py` as ONE background exec cell; **(2)** poll its log until the
   cell exits — **polls carry no analysis**; **(3)** on exit 0 read
   `DRIVER-STATE.json` and report its summary, then run Stage 8 (vault copy + HTML +
   `ledger.py append` of `80-ledger-row.json`) **only if `ledger_row` is non-null and
   `ledger_row_error` is absent** — a null row means the ensemble never reached a
   final tally, so publish nothing and report `ledger_row_error.code` instead. If
   `status` is `published-ready-with-qa-exceptions`, say so; never call it a clean
   pass. Exit 10 → act on the named `reason.code`; exit 20 → abstain report +
   `no_call` row; exit 2 → fix the invocation.
4. Prohibited on this host: **never `spawn_agent` for a pipeline role**, never
   re-implement a stage inline (fix the cause and re-run `--resume`), never poll
   individual workers. There is one thing to poll: the driver's log.
5. No Artifact tool on Codex → the deliverable is `render_report.py`'s local HTML
   plus the vault copy; footer notes `artifact: local-html` and
   `cost: cursor-subscription (N/A)`.
6. Single-ticker, live mode only. Batch/portfolio, the daily monitor, crypto,
   `--options`, and historical as-of replay stay claude-code-only — the driver
   rejects them at parse time with exit 2.

This entry is a pointer: all pipeline logic lives in SKILL.md, not here.
