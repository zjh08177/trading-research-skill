# Batch / portfolio pipeline

Drivers that run the single-ticker v2 pipeline across a whole portfolio. The
skill (`../../SKILL.md`) is single-name; this layer fans it out over the book.
See vault `Projects/personal/tradingagents/v2-skillify/impl-plan/impl-plan-report-ux.md`.

| Script | Role |
|---|---|
| `snapshot_holdings.py` | Write the day's holdings snapshot (the single holdings SSOT) to `reports/portfolio/holdings-history/YYYY-MM-DD.json` — subprocesses the SnapTrade holdings CLI under the skill venv, wraps stdout verbatim in a dated envelope, iCloud-safe write. Args: `<holdings_history_dir> [asof]`. Env: `SNAPTRADE_HOLDINGS_PY` / `SNAPTRADE_HOLDINGS_CLI`. |
| `portfolio_delta.py` | Diff the two latest snapshots into New/Exited/Added/Trimmed rows and grade them against the ledger rating + stateful monitor triggers (§3.1 adherence matrix). Hold-rated size changes without a `confirmed_act` sidecar are discipline issues, not neutral no-calls. Args: `<holdings_dir> <ledger.jsonl> <sidecar_dir> <out_md>`. Emits `delta-<date>.{md,json}`. |
| `classify_holdings.py` | Split a SnapTrade holdings dump into the analyzable deep-dive set (drop cash MMFs/junk) + a sector map; emit `classmap.json`. |
| `build_datapack.py` | Per equity/ADR/ETF ticker: run vendor CLIs → merge → derive mcap/PE → tiingo cross-check → `10-datapack.*` + `15-position.*`. Arg: JSON `[[ticker,kind],...]`. |
| `build_crypto_pack.py` | Per crypto: turn a saved Crypto.com raw JSON (`{ticker,candles}`, fetched via MCP by the orchestrator) into `10-datapack.*` + `15-position.*`. Args: `<TICKER> <raw.json> <holdings.json> [asof] [stamp]`. |
| `build_dossier.py` | Assemble the self-contained HTML dossier (overview scorecard + every `60-report.md` via `render_report.md_to_html` + client nav). |
| `publish_ledger.py` | Copy each `60-report.md` to the vault **canonical per-name location** `reports/single-ticker/<TICKER>/<TICKER>-<asof>.md` (vault `reports/_index.md` taxonomy; mirrors the replay publisher's `reports/replay/<T>/`) + append one look-ahead-guarded ledger row per ticker (its `report_path` matches). Book-level dossiers go to `reports/portfolio/portfolio-dossier-<asof>.html`. Never write reports flat at the `reports/` root. |
| `../../workflows/portfolio_pipeline.js` | Workflow: analysts→debate→risk→N=5 opus judges→tally→writer→QA, pipelined across all tickers. Writers emit schema-v2 `LEVELS_JSON`, not legacy flat `LEVELS:`. |
| `../../workflows/rewrite_writers.js` | Workflow: re-run only the opus writer on existing runs (reuse cached ratings) — for prompt/format upgrades without re-judging. |

**Caveat (2026-07 batch):** `build_datapack.py`, `build_dossier.py`, `publish_ledger.py`
carry hardcoded `SK`/`VAULT`/job-tmp `HOLD`/`ASOF`/`STAMP` constants from the first
full-book run. Parameterize (argv/env) before reusing on a new date — tracked as a
follow-up atom, not a blocker for version-control.
