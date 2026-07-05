# Batch / portfolio pipeline

Drivers that run the single-ticker v2 pipeline across a whole portfolio. The
skill (`../../SKILL.md`) is single-name; this layer fans it out over the book.
See vault `Projects/personal/tradingagents/v2-skillify/impl-plan/impl-plan-report-ux.md`.

| Script | Role |
|---|---|
| `classify_holdings.py` | Split a SnapTrade holdings dump into the analyzable deep-dive set (drop cash MMFs/junk) + a sector map; emit `classmap.json`. |
| `build_datapack.py` | Per equity/ADR/ETF ticker: run vendor CLIs → merge → derive mcap/PE → tiingo cross-check → `10-datapack.*` + `15-position.*`. Arg: JSON `[[ticker,kind],...]`. |
| `build_crypto_pack.py` | Per crypto: turn a saved Crypto.com raw JSON (`{ticker,candles}`, fetched via MCP by the orchestrator) into `10-datapack.*` + `15-position.*`. Args: `<TICKER> <raw.json> <holdings.json> [asof] [stamp]`. |
| `build_dossier.py` | Assemble the self-contained HTML dossier (overview scorecard + every `60-report.md` via `render_report.md_to_html` + client nav). |
| `publish_ledger.py` | Copy each `60-report.md/.html` to the vault + append one look-ahead-guarded ledger row per ticker. |
| `../../workflows/portfolio_pipeline.js` | Workflow: analysts→debate→risk→N=5 opus judges→tally→writer→QA, pipelined across all tickers. |
| `../../workflows/rewrite_writers.js` | Workflow: re-run only the opus writer on existing runs (reuse cached ratings) — for prompt/format upgrades without re-judging. |

**Caveat (2026-07 batch):** `build_datapack.py`, `build_dossier.py`, `publish_ledger.py`
carry hardcoded `SK`/`VAULT`/job-tmp `HOLD`/`ASOF`/`STAMP` constants from the first
full-book run. Parameterize (argv/env) before reusing on a new date — tracked as a
follow-up atom, not a blocker for version-control.
