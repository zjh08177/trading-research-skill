# trading-research — a Claude Code skill

Grounded, adversarial single-name research for stocks: deterministic data pack →
parallel analysts → bull/bear debate → computed risk box → N≥3 independent judge
ensemble → cited report with a track-record ledger. Decision-support only — you
decide and execute; the tool informs. **Not financial advice.**

## Requirements

- [Claude Code](https://claude.com/claude-code) with a plan that allows subagents
  (the pipeline spawns Sonnet analysts and Opus judges via the Agent tool).
- Python 3.13 (`brew install python@3.13`).
- Your own market-data API keys (below). The skill is fully self-contained —
  no other checkout or install is needed.

## Install

```sh
git clone https://github.com/zjh08177/trading-research-skill.git \
  ~/.claude/skills/trading-research
~/.claude/skills/trading-research/scripts/setup_venv.sh
```

## Credentials

Create `~/.config/tradingagents/` (chmod 700) with:

`vendors.env` — core market data:

```
SCHWAB_CLIENT_ID=...          # Schwab developer app (price/bars/options)
SCHWAB_CLIENT_SECRET=...
SCHWAB_TOKEN_PATH=~/.config/tradingagents/schwab_token.json
TIINGO_API_KEY=...            # out-of-band price cross-check
MARKETAUX_API_KEY=...         # headlines (free tier OK)
SEC_EDGAR_USER_AGENT="Name email@example.com"   # SEC requires a contact UA
```

`unusualwhales.env` (optional — enables `--options` dealer-positioning pack):

```
UNUSUAL_WHALES_API_KEY=...
```

`snaptrade.env` (optional — position-aware reporting; omit to run position-blind):

```
SNAPTRADE_CLIENT_ID=...
SNAPTRADE_CONSUMER_KEY=...
SNAPTRADE_USER_ID=...
SNAPTRADE_USER_SECRET=...
```

Set the track-record ledger path (shell profile or Claude Code env):

```sh
export TRADING_RESEARCH_LEDGER=~/trading-reports/ledger.jsonl
```

## Run

In Claude Code:

```
/trading-research NVDA
/trading-research MRVL --options
```

Missing sources degrade gracefully (named `MISSING`/`DEGRADED` sections), never
silently. Run artifacts land in `runs/<TICKER>-<date>-<hhmm>/` (git-ignored —
they can contain live account data if SnapTrade is configured).

## Scope notes

- Single-ticker research is fully portable. The batch/portfolio drivers under
  `scripts/batch/` pin the author's machine paths — treat them as reference.
- Every access is read-only; no order or trade endpoint is ever referenced.
