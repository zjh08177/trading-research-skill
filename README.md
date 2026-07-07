# trading-research — a Claude Code skill

Ask for a stock, get an institutional-grade research report you can argue with.
Decision-support only — you decide and execute; the tool informs. **Not financial advice.**

## Part 1 — What it does

![Agent graph: one sealed data pack travels unaltered through analysts, a bull/bear tug-of-war, a computed risk gauge, and three independent judges voting, ending in a report glued into a running ledger](assets/architecture-xiaohei.png)

*The whole architecture in one sketch: one sealed data pack (「同一份数据」, never rewritten —「不改一字」) gets argued over by a bull and a bear (「多空拉扯」) under a computed risk gauge, then three independent judges each cast their own vote (「各投各的」), and the verdict lands in a cited report stitched to a running ledger (「报告+台账」).*

Type `/trading-research NVDA` in Claude Code and a staged multi-agent pipeline
produces a cited, adversarial research report grounded in live market data:

- **Live data pack, fail-loud.** Price and technicals (Schwab), SEC fundamentals
  (EDGAR), dated headlines and next earnings, an independent Tiingo price
  cross-check. Every number in the report traces to a tagged pack fact; a dead
  source becomes a named `MISSING`/`DEGRADED` box, never a silent guess.
- **Adversarial, not agreeable.** Three analysts (fundamental / technical /
  sentiment) brief independently, then a bull and a bear debate in two waves —
  the strongest case for each side, attacked directly.
- **A jury, not a vibe.** Three to five independent judges vote blind on
  byte-identical inputs. The headline rating is the ensemble tally with the full
  vote distribution and dissent published. Wide disagreement escalates the panel;
  irreconcilable spread publishes as an honest NO-CALL.
- **Risk in ATR units.** A computed risk box states the adverse move, volatility
  context, and invalidation anchor. Every report carries **two-sided decision
  levels** — a downside that breaks the thesis and an upside that upgrades it.
- **Options X-ray (`--options`).** Dealer positioning from Unusual Whales: net
  GEX and gamma regime, gamma flip, IV rank and term structure, max pain, OI
  walls, unusual flow — rendered deterministically, never allowed to change the
  equity rating.
- **Position-aware, position-blind.** With SnapTrade linked (read-only, any
  broker), the report adds a "Your position" section — size, P/L, a two-sided
  dollar plan, tax flag. The rating itself never sees your position.
- **A track record you can audit.** Every run appends to a ledger; each new run
  reads its own history with a look-ahead guard. Ratings age publicly, in front
  of you.

## Part 2 — Setup & use

### Requirements

- [Claude Code](https://claude.com/claude-code) with a plan that allows
  subagents (analysts run on Sonnet, judges on Opus).
- Python 3.13 (`brew install python@3.13`).
- Your own market-data API keys (below). The skill is self-contained — no other
  checkout or install needed.

### Install

```sh
git clone https://github.com/zjh08177/trading-research-skill.git \
  ~/.claude/skills/trading-research
~/.claude/skills/trading-research/scripts/setup_venv.sh
```

### Credentials

Create `~/.config/tradingagents/` (`chmod 700`) with:

`vendors.env` — only Schwab is required; every optional source you skip shows
up as a named Data Gap in the report instead of failing the run:

```
# REQUIRED — free: register a developer app at developer.schwab.com
# (needs a Schwab brokerage account; the API itself costs nothing)
SCHWAB_CLIENT_ID=...
SCHWAB_CLIENT_SECRET=...
SCHWAB_TOKEN_PATH=~/.config/tradingagents/schwab_token.json

# REQUIRED but not a key — SEC just wants a contact string (free)
SEC_EDGAR_USER_AGENT="Name email@example.com"

# OPTIONAL — free tiers
TIINGO_API_KEY=...            # out-of-band price cross-check
MARKETAUX_API_KEY=...         # headlines
```

`unusualwhales.env` (optional, paid — unlocks `--options` dealer positioning):

```
UNUSUAL_WHALES_API_KEY=...
```

`snaptrade.env` (optional — unlocks position-aware reports; omit to run
position-blind). SnapTrade is free for one linked brokerage connection. Link
yours once with `.venv/bin/python scripts/vendors/snaptrade_setup.py`:

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

### Use

In Claude Code:

```
/trading-research NVDA               # full equity report
/trading-research MRVL --options     # + dealer-positioning pack
/trading-research MRVL --options-only  # zero-LLM options audit block, no rating
```

Reports render as a styled HTML page plus canonical markdown; run artifacts land
in `runs/<TICKER>-<date>-<hhmm>/` (git-ignored — they can contain live account
data when SnapTrade is configured).

### Scope notes

- Single-ticker research is fully portable. The batch/portfolio drivers under
  `scripts/batch/` pin the author's machine paths — treat them as reference.
- Every broker access is read-only; no order or trade endpoint is ever
  referenced (asserted by tests).
