---
name: trading-research
description: >-
  This skill should be used when the user asks for a stock or crypto research
  report, a buy/sell/hold opinion, a thesis stress-test, a two-ticker
  comparison, an earnings/event reaction, or invokes `/trading-research
  <ticker>`. It runs a staged multi-agent pipeline — deterministic data pack,
  parallel analysts, bull/bear debate, ATR-normalized risk box, an N=3-5 Opus
  judge ensemble with full dissent, and a cited institutional report — grounded
  in live market data, and appends every run to a track-record ledger.
---

# Trading research pipeline

Produce a grounded, adversarial research report with an ensemble rating and its
full vote distribution. The user decides and executes; this skill informs, not
an autotrader. Every stage persists an artifact; the three failure-prone edges
(ensemble tally, ledger look-ahead guard, citation check) are scripts, not prose.
Load `references/prompts.md` for the role cards and `references/report-template.md`
for the report skeleton.

## Pipeline

Run stages in order. Each stage reads and writes files in the run folder; the
artifacts are the only channel between stages.

| Stage | Actor | Model | Reads | Writes |
|---|---|---|---|---|
| 0 Scope | orchestrator | session | query | `00-scope.md` (job class, tickers, asset class; ambiguity → AskUserQuestion once) |
| 1 Data pack | orchestrator via `scripts/vendors/*` CLIs; fallback finance skills/MCP | session | live tools | `10-datapack.md` + `.json` |
| 1b Position | orchestrator via `scripts/vendors/schwab_account.py`; current-day only | session | live tool | `15-position.md` + `.json` (WITHHELD from stages 2–5) |
| 2 Analysts ×3 | Agent tool, parallel | sonnet | full pack verbatim | `20-analyst-{fund,tech,sent}.md` |
| 3 Debate | bull + bear agents, parallel, 2 waves | sonnet | pack + analyst briefs | `30-debate.md` |
| 4 Risk box | risk-officer agent | sonnet | pack + debate | `40-risk.md` |
| 5 Ensemble | N judge agents, parallel, byte-identical inputs | opus | pack + briefs + debate + risk + guarded track record | `50-votes/vote-{1..N}.md` |
| 5b Tally | `scripts/ensemble.py` | — | votes | `55-rating-block.md` (inserted into report VERBATIM) |
| 6 Report | writer agent | opus | all artifacts + template + `15-position.json` | `60-report.md` |
| 7 QA | `scripts/qa_check.py` + 1 sonnet prose pass | sonnet | report + `10-datapack.json` + `15-position.json` | `70-qa.txt` |
| 8 Publish + ledger | orchestrator + `scripts/ledger.py` | — | report | vault copy + ledger row |

Run folder: `runs/<TICKER>-<date>-<hhmm>/`. Final report copies to the vault
`reports/` folder; the ledger lives at vault `reports/ledger.jsonl`.

Resume rule: on crash, stat the artifacts in order and restart at the first
missing file. There is no resume machinery beyond this rule.

## Invariants

Enforce all fifteen. Any violation is a defect, not a judgment call.

| # | Rule | Enforced by |
|---|---|---|
| 1 | No judgment single-samples: headline rating comes only from `ensemble.py` over N≥3 votes. | script emits `55-rating-block.md`; writer inserts verbatim; alteration = QA defect |
| 2 | Judges receive byte-identical inputs; orchestrator never summarizes, paraphrases, or "repairs" any agent output. | SKILL.md invariant + artifacts are the only inter-stage channel |
| 3 | Ledger is read/written only through `ledger.py`; reads filter `date_utc < as_of` (two-sided guard). | script is sole entry point; the `~/.tradingagents/memory/` path is BANNED |
| 4 | Numbers in the report carry `[P#.fact]` tags or a URL; untagged numbers fail QA. | `qa_check.py` vs `datapack.json` |
| 5 | Dead data source → named `MISSING(reason)` section + Data Gaps box; dead P1 → abstain report + `no_call` ledger row. Never silent. | pack contract + failure map |
| 6 | Move/level language states ATR14 multiples before any escalation word. | prompts.md role cards + QA prose pass |
| 7 | Footer discloses: actual N, agent count, model mix, wall clock, token cost. Thin ensemble (<3 valid votes) is never presented as N≥3. | run-stats collection + template slot |
| 8 | Escalation (spread ≥2 at N=3 → N=5) always runs; R7 overrun is disclosed, never skipped. | `ensemble.py` decision output |
| 9 | Spread ≥3 at N=5 → headline `NO-CALL`, distribution still published + ledger-logged. | `ensemble.py` |
| 10 | P1–P5 fill from the named vendor CLIs; every fallback-filled fact stamps its real `src` and the section is boxed `DEGRADED(P#, reason)` in Data Gaps. P1 carries a tiingo cross-check stamp: same-asof-date closes within 0.5% → CROSS-CHECK OK, else CROSS-CHECK FAIL named in Data Gaps (run continues). | CLIs exit nonzero and never fabricate; orchestrator stamps src + gaps |
| 11 | Current-day runs use `schwab_quote.py` `P1.last` (real trade-time) as the price headline; box it `DELAYED` when `P1.is_realtime` is false and `STALE(as-of <date>)` when its trade-date precedes `as_of`. Prior close and chg% derive from `schwab_bars.py` settled bars, never the quote. The live quote is valid only when `as_of` is today — the CLI refuses a past/future `as_of` (exit 3), so back-dated runs use settled bars. When `P1.last` is absent (back-dated or quote-failed), the headline cites `[P1.price]` `settled close` — never a tag missing from the pack. | `schwab_quote.py` guard (as_of==today, parsed dates); writer picks `[P1.last]`/`[P1.price]` by pack presence; `tiingo_oracle.py --live` `P1.px_last_oob` cross-checks |
| 12 | Position facts (`15-position.*`) are withheld from analysts, debate, risk, and judges; only the writer and `qa_check.py` read them. The rating is position-blind. | stage read-sets above; artifact is never merged into `10-datapack.*` |
| 13 | Account access is read-only: `GET /accounts` (+ `/accounts/accountNumbers`) only. No order, trade, or mutation endpoint is ever referenced. | CLI holds no order path; `test_schwab_account.py` asserts absence |
| 14 | Position is live-only: a past/future `--asof` yields no position (exit 3), never a fabricated historical holding. | `schwab_account.py` `--asof` guard (parsed dates vs today) |
| 15 | The position never changes the headline call — only the action framing around it. A "Your Position" section that argues the rating is a defect. | writer role card; QA prose pass |

## Byte-identical inputs and no paraphrase

Assemble the judge input once (pack + analyst briefs + debate + risk + guarded
track record) and pass the same bytes to every judge. Never summarize,
paraphrase, re-order, or "clean up" an agent's output — route it as-is through
the run-folder artifact. When a stage produces nothing, record the gap; never
invent its content.

## Data pack

Fetch the pack first (Stage 1) and inject it verbatim into every downstream
agent. Key `10-datapack.json` by fact id — `{"P2.atr14": {"v": 19.86, "unit":
"USD", "asof": "...", "src": "schwab"}}` — and render the same facts
in `10-datapack.md`. Flag any section past its staleness threshold as `STALE`.

| § | Content | Source | STALE when |
|---|---|---|---|
| P1 | live price + day range/vol, chg%, 52wk, mcap (derived: price × EDGAR shares), avg vol | `vendors/schwab_quote.py` (live `P1.last`) + `vendors/schwab_bars.py` (settled close, chg%, 52wk) + `tiingo_oracle.py --live` cross-check; fallback stock-market-pro; crypto: Crypto.com MCP | quote trade-date < `as_of`; crypto >15 min |
| P2 | SMA20/50/200, RSI14, MACD, ATR14 (abs+%), 30d σ | `vendors/schwab_bars.py`; fallback stock-market-pro | same as P1 |
| P3 | rev/EPS TTM+YoY, margins, FCF, net debt, P/E (derived) | `vendors/edgar_fundamentals.py`; fallback stock-market-pro; crypto: `MISSING(by-design)` | >100 days |
| P4 | ATM IV + term slope, put/call vol+OI, notable OI | `vendors/schwab_options.py`; fallback stock-market-pro; crypto: N/A | >1 trading day |
| P5 | ≤10 dated headlines + next earnings date | `vendors/marketaux_news.py` + WebSearch (earnings date); fallback stock-market-pro news | headline >14d dropped; event job >48h flagged |
| P6 | sentiment (equity: news tone; crypto: LunarCrush) | LunarCrush MCP / derived | >1 day |
| P7 | track record | `ledger.py read --ticker X --before <as_of>` | guard is code, not prose |

Vendor CLIs: run `<UPSTREAM>/.venv/bin/python scripts/vendors/<cli>.py --ticker X --asof <date>`
(UPSTREAM = the TradingAgents-upstream repo). Each prints one JSON object of facts on
stdout or exits nonzero with a one-line stderr reason — merge stdout objects into
`10-datapack.json` as-is. Nonzero exit → retry once → fall back per the table and
stamp `DEGRADED(P#, reason)`. List-valued facts (P4.iv_term, P4.notable_oi,
P5.headlines) are context, never numerically tagged in the report.

For P1 on a current-day run, also run `schwab_quote.py` (live `P1.last` + day
range/vol; refuses a past `--asof` so back-dated runs use settled bars) and add
`--live` to `tiingo_oracle.py` (emits the `P1.px_last_oob` cross-check). Use
`P1.last` as the price headline with its trade-time as-of; keep `P1.price`
(settled) as the prior-close/chg% base per invariant 11.

## Position (Stage 1b, current-day only)

Run `<UPSTREAM>/.venv/bin/python scripts/vendors/schwab_account.py --ticker X --asof <date>`
after the pack. Its `H1` facts (weight, cost basis, unrealized P/L, % of book)
go to a SEPARATE artifact (`15-position.md` + `.json`), never merged into
`10-datapack.*` — withheld from analysts, debate, risk, and judges (invariant
12); only the writer and `qa_check.py` read it. Held → full `H1.*`; flat →
`{"H1.held": false}` (cold-start report unchanged); back-dated or auth-fail → no
artifact, noted in Data Gaps. Read-only: GET `/accounts` only (invariant 13).
When Stage 1b wrote the artifact, pass it to QA as `qa_check.py 60-report.md 10-datapack.json 15-position.json`; otherwise (flat wrote a file too, but back-dated/auth-fail runs write none) use the 2-arg form. `qa_check.py` tolerates an absent position path either way.

## Ledger

Read and write the ledger only through `scripts/ledger.py`. Set its path via the
`TRADING_RESEARCH_LEDGER` env var (vault `reports/ledger.jsonl`); there is no
default fallback. Fetch P7 with `ledger.py read --ticker <T> --before <as_of>`,
passing the run's `as_of` (the look-ahead guard). Append one row per run
(including aborts) with `ledger.py append`; on write failure it prints the row
with a MANUAL-APPEND banner and exits 2 — append that row by hand, never skip.

## Ensemble tally

Spawn N=3 judges, write each vote to `50-votes/vote-<i>.md` ending in exactly one
`VERDICT: … | CONVICTION: … | WHY: …` line, then run `ensemble.py tally 50-votes
--n-target 3`. Act on the JSON decision on stderr: `escalate` → spawn 2 more
judges and re-run with `--n-target 5`; `no-call` → publish the distribution under
a NO-CALL headline; `publish` → done. Respawn a malformed vote once, then drop and
disclose it. Insert the emitted `55-rating-block.md` into the report verbatim.

## Failure map

| Failure | Behavior |
|---|---|
| One analyst/debater dies | proceed; role listed in Data Gaps; never re-invented by orchestrator |
| Judge malformed/hung | respawn once → drop + disclose N |
| P1 unfillable | abstain report + ledger row (`no_call`, `gaps:["price"]`) |
| Non-P1 section dead after 1 retry | `MISSING(reason)`, run continues |
| Vendor CLI nonzero exit | retry once → free-tool fallback; facts stamp real `src`; `DEGRADED(P#, reason)` in Data Gaps |
| Tiingo cross-check FAIL or unavailable | named in Data Gaps (`CROSS-CHECK FAIL/UNAVAILABLE`); run continues; never triggers P1 fallback alone |
| `schwab_account.py` exit 2 (auth/reauth) | "Your Position" omitted + noted in Data Gaps; run continues position-blind |
| `schwab_account.py` exit 3 (back-dated `--asof`) | no position section (expected for back-dated runs); run continues |
| Ticker not held (`H1.held=false`) | no position section; cold-start report unchanged |
| qa_check hard fail ×2 | ship with QA-exceptions box quoting failures verbatim |
| ledger append fails | print row in chat; never skip silently |
| Wall clock > 15 min | finish + disclose overrun (never abort for time) |

## Model tiers

Single source of truth for who runs on what.

| Work | Model |
|---|---|
| Orchestrator | session model |
| Analysts, debaters, risk officer, QA prose pass | sonnet |
| Judges, writer | opus |

## Disclosure footer

Every report ends with a footer stating: actual N (valid votes), total agent
count, model mix, wall-clock time, token cost, and "not financial advice." A
thin ensemble (<3 valid votes) is disclosed as such and never presented as N≥3.
