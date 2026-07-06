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
| 1b Position | orchestrator via `scripts/vendors/snaptrade_account.py` (cross-broker; `schwab_account.py` fallback); current-day only | session | live tool | `15-position.md` + `.json` (WITHHELD from stages 2–5) |
| 2 Analysts ×3 | Agent tool, parallel | sonnet | full pack verbatim | `20-analyst-{fund,tech,sent}.md` |
| 3 Debate | bull + bear agents, parallel, 2 waves | sonnet | pack + analyst briefs | `30-debate.md` |
| 4a Risk box (computed) | `scripts/risk_box.py` | — | `10-datapack.json` | `40-riskbox-block.md` (inserted into report VERBATIM) |
| 4b Risk narrative | risk-officer agent | sonnet | pack + debate + `40-riskbox-block.md` | `40-risk.md` (leads with the verbatim block, then narration) |
| 5 Ensemble | N judge agents, parallel, byte-identical inputs | opus | pack + briefs + debate + `40-risk.md` (leads with the verbatim risk box) + guarded track record | `50-votes/vote-{1..N}.md` |
| 5b Tally | `scripts/ensemble.py` | — | votes | `55-rating-block.md` (inserted into report VERBATIM) |
| 6 Report | writer agent | opus | all artifacts + template + `15-position.json` | `60-report.md` |
| 7 QA | `scripts/qa_check.py` + 1 sonnet prose pass | sonnet | report + `10-datapack.json` + `15-position.json` | `70-qa.txt` |
| 7b Render | `scripts/render_report.py` | — | `60-report.md` | `60-report.html` (self-contained styled page) |
| 8 Publish + ledger | orchestrator + `scripts/ledger.py` + Artifact | — | report | HTML Artifact + vault copy (`.md`+`.html`) + ledger row |

Run folder: `runs/<TICKER>-<date>-<hhmm>/`. Both `60-report.md` and `60-report.html`
copy to **`reports/single-ticker/<TICKER>/`** in the vault (options runs →
`reports/options/<TICKER>/`; book-level monitor/action-plan/dossier →
`reports/portfolio/`). Code state stays at the vault `reports/` root — `ledger.jsonl`,
`levels/`, `classmap.json` are read at fixed paths; never move them. See
`reports/_index.md` for the full layout.

**Report delivery is HTML.** Markdown stays the CANONICAL artifact — the writer
emits `60-report.md`, and `qa_check.py` + `ledger.py` parse it (all `[P#.fact]`
cite-tag machinery depends on markdown headings/tables). After QA passes, Stage 7b
runs `render_report.py 60-report.md` → `60-report.html` (a self-contained, styled
page; no external assets). Stage 8 publishes that HTML via the **Artifact tool** as
the primary deliverable and copies BOTH files to `reports/single-ticker/<TICKER>/`.
Never hand-author the HTML or let an agent regenerate it — the renderer is
deterministic so the delivered page always matches the QA'd markdown byte-for-byte
in content. For a portfolio/batch run, assemble one dossier (overview scorecard +
every `60-report.md` via `render_report.md_to_html`), publish it as a single
Artifact, and copy it to `reports/portfolio/`.

Resume rule: on crash, stat the artifacts in order and restart at the first
missing file. There is no resume machinery beyond this rule.

## Invariants

Enforce all sixteen. Any violation is a defect, not a judgment call.

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
| 13 | Account access is read-only across every position source: the CLIs list accounts + positions only (Schwab `GET /accounts`; SnapTrade `list_user_accounts` + `get_all_account_positions`). No order, trade, or mutation endpoint is ever referenced. | CLIs hold no order path; `test_schwab_account.py` + `test_snaptrade_account.py` assert absence |
| 14 | Position is live-only: a past/future `--asof` yields no position (exit 3), never a fabricated historical holding. | `snaptrade_account.py` / `schwab_account.py` `--asof` guard (parsed dates vs today) |
| 15 | The position never changes the headline call — only the action framing around it. A "Your Position" section that argues the rating is a defect. | writer role card; QA prose pass |
| 16 | The risk box's adverse-move, invalidation, and context numbers come only from `risk_box.py`, inserted verbatim as `40-riskbox-block.md`; the risk officer narrates around it and never recomputes them. The block is context-only — never an action/size, never changes the rating. | `risk_box.py` emits the block; officer card forbids recompute; `qa_check.py` exempts the verbatim region |

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
| P8 | dealer GEX + gamma regime/flip, IV rank/skew/term, max pain, OI walls, live flow (`--options` only) | `vendors/uw_options.py` (Unusual Whales); suppresses P4 on success | per-fact daily/snapshot; live facts session-gated |

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

Run `<UPSTREAM>/.venv/bin/python scripts/vendors/snaptrade_account.py --ticker X --asof <date>`
after the pack — the cross-broker source (Robinhood, Schwab, Fidelity, … via
SnapTrade). It aggregates the LONG holding across every linked account. Its `H1`
facts (qty, avg cost, unrealized P/L, % of book, plus `H1.brokers` and
`H1.n_accounts`) go to a SEPARATE artifact (`15-position.md` + `.json`), never
merged into `10-datapack.*` — withheld from analysts, debate, risk, and judges
(invariant 12); only the writer and `qa_check.py` read it. Held → full `H1.*`
(the three P/L facts are omitted only when a linked account gives no cost basis);
flat → `{"H1.held": false}` (cold-start report unchanged); back-dated or
auth-fail → no artifact, noted in Data Gaps. Read-only: lists accounts +
positions only, no order path (invariant 13).

**Fallback:** if `snaptrade_account.py` exits 2 (unconfigured/auth), fall back to
`schwab_account.py --ticker X --asof <date>` (Schwab-only, `src: schwab`). Use ONE
source, never both — SnapTrade already aggregates Schwab if the owner linked it, so
summing would double-count. The fact `src` stamp records which source ran.

When Stage 1b wrote the artifact, pass it to QA as `qa_check.py 60-report.md 10-datapack.json 15-position.json`; otherwise (back-dated/auth-fail runs write none) use the 2-arg form. `qa_check.py` tolerates an absent position path either way.

## Ledger

Read and write the ledger only through `scripts/ledger.py`. Set its path via the
`TRADING_RESEARCH_LEDGER` env var (vault `reports/ledger.jsonl`); there is no
default fallback. Fetch P7 with `ledger.py read --ticker <T> --before <as_of>`,
passing the run's `as_of` (the look-ahead guard). Append one row per run
(including aborts) with `ledger.py append`; on write failure it prints the row
with a MANUAL-APPEND banner and exits 2 — append that row by hand, never skip.

## Risk box (computed)

Before spawning the risk officer, run `<UPSTREAM>/.venv/bin/python scripts/risk_box.py
10-datapack.json > 40-riskbox-block.md`. It computes the adverse move (ATR14 / 30d σ
multiples), the today-move/ATR ratio, the SMA50±ATR invalidation anchor, and a
NORMAL/ABNORMAL context flag from pack facts — the numbers the officer used to
compute by hand (the FIND-1 surface). A missing required fact → exit 3 (fail loud).
Pass the block to the officer, who narrates around it and never recomputes it, and
insert it VERBATIM into the report's `## Risk box` slot. The block is context-only:
it never states an action or size and never changes the rating (invariant 16).

## Options analysis (`--options` / `--options-only`)

Two opt-in flags add an Unusual Whales dealer-positioning read — the **P8** pack.
Off by default (added latency + tokens); the light Schwab P4 IV stays the
standing options source until a run opts in.

**`--options` (add-on)** — single-ticker orchestrator flow:

1. **Stage 1**: after the P1/P2 pack is built, run `<UPSTREAM>/.venv/bin/python
   scripts/vendors/uw_options.py --ticker X --spot <P1.last, fallback P1.price>
   --atr <P2.atr14> [--earnings <P5 date if resolved>]`. Merge every `P8.*` fact
   (scalars AND context lists) into `10-datapack.json` + a `## P8` section of
   `10-datapack.md`; route `P8._gaps` into Data gaps and keep it in the json so
   `render_options` echoes it in the block. On P8 success **skip
   `schwab_options`** — P4 is suppressed; emit the Schwab IV only on a NAMED P8
   gap, stamped `src=schwab` (D2/EC4).
2. **Stages 2–5**: agents receive P8 verbatim and may cite positioning; the
   emitted rating stays the equity Buy/Sell/Hold — options never change it.
3. **Stage 6a**: the orchestrator runs `render_options.py 10-datapack.json >
   52-options-block.md` (mirrors risk_box→`40-`, ensemble→`55-`).
4. **Stage 6**: the writer inserts `52-options-block.md` VERBATIM into the
   `## Dealer Positioning & Options` slot — never regenerates it (Invariant 2).
5. **Stage 7**: `qa_check` verifies every tagged P8 scalar via `check_pairs`;
   `scan_untagged` skips the block (context tables are untagged by design).
6. **Stage 8**: ledger unchanged; the live-flow P8 facts already sit in
   `10-datapack.json`, so the thesis hash stays reconstructable (D3).

**`--options-only` (standalone)** — a zero-LLM spine:

```
0 scope → 1 uw_options.py --spot <quote, fallback settled close> --atr <bars ATR>
        --earnings <P5 lookup> → render_options.py
        → runs/<T>-<date>/options-only-<hhmm>.{md,json}  (non-scored audit artifact)
NEVER calls an Agent, ensemble.py, or ledger.py (O2/EC2/EC8).
```

Fetch pacing: `uw_options` paces its own ~17 UW calls at ≥0.75 s (≈80/min, under
the ~120/min ceiling); the batch driver runs tickers serially, so no cross-ticker
burst.

Options invariants (enforce alongside 1–16):

| # | Rule |
|---|---|
| O1 | Every P8 fact stamps `daily`/`snapshot`/`live`; a snapshot/live fact is never rendered as a trend or backtest claim. |
| O2 | `--options-only` appends NO ledger row and forces NO rating; it writes a separate non-scored audit artifact. |
| O3 | P8 reuses `check_pairs` + the `%`→`/100` ratio rule; numbers render full-digit; list facts are context-only (never number-tagged); the only exemption is the `scan_untagged` block skip. |
| O4 | Net GEX = call_gamma+put_gamma (UW type-signs); sign drives {long/short}-gamma → {dampen/amplify}; flip is a proximity flag, not a co-input; sign vs (spot≥flip) disagreement → `gex_data_inconsistent`. Golden-fixture verified. |
| O5 | Front-expiry implied move is tagged event-inclusive when it spans a known earnings date; earnings absent → `event-status-unknown` + a gap, never silently event-clean. |
| O6 | RR skew = call_IV − put_IV (negative under put skew); the fact labels direction, not just magnitude. |
| O7 | Below a per-group data floor (min OI, ≥2 expiries, IV-rank present) the group emits `DATA-THIN(group)` — never a computed regime/rank on a degenerate payload. |
| O8 | Every `live` fact carries a session-state; cumulative intraday metrics gate to STALE/DATA-THIN when the session is incomplete or absent. |
| O9 | Output describes positioning and levels; it NEVER names a strike or expiry to buy or sell. |

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
| `snaptrade_account.py` exit 2 (unconfigured/auth) | fall back to `schwab_account.py`; if that also exits 2, "Your Position" omitted + noted in Data Gaps; run continues position-blind |
| Position CLI exit 3 (back-dated `--asof`) | no position section (expected for back-dated runs); run continues |
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

## Host runtimes

ADDITIVE to everything above. The whole pipeline runs unchanged on **Claude Code**
(the default host). This section maps it onto a **Cursor** session, which has no
Agent or Artifact tool — every agent stage becomes a `cursor-agent -p` shell call.
A runner that is not on Cursor ignores this section; Claude Code behaviour is
byte-identical to today.

**Scope (R4):** Cursor host is **single-ticker only**. Batch/portfolio, the daily
invalidation monitor, and crypto tickers (Crypto.com MCP) stay **claude-code-only**.

**Install:** symlink `hosts/cursor-command.md` → `~/.cursor/commands/trading-research.md`.

**Host detection:** the runner self-identifies; default is `host=claude-code` and
everything above reads exactly as today.

### Model slots (Cursor host) — slugs from `cursor-agent --list-models`

| Stage | Model slug |
|---|---|
| Judge 1 / 2 / 3 (panel) | `gpt-5.5-extra-high` / `claude-opus-4-8-thinking-max` / `glm-5.2-high` |
| Judge 4 / 5 (escalation) | `composer-2.5` / `grok-4.3` |
| Analysts, debate, risk officer, QA-fix | `composer-2.5` |
| Writer | `claude-opus-4-8-thinking-high` |

All slots bill the Cursor subscription — no Anthropic API/Max usage. Slugs churn:
this table is the one place to fix a renamed slot.

### Judge invocation (Stage 5) — probe-verified

Each judge runs in the BACKGROUND against a temp file; after `wait`, all votes are
moved into `50-votes/` together so a later-finishing judge never reads a sibling
vote (parallel + isolation, Claude Code parity):

```bash
# per judge n, slug $slug; NO --sandbox flag (fatal on this machine); plan mode is read-only
{ printf 'BACKEND: cursor\nMODEL: %s\nSLOT: %s\n\n' "$slug" "$n";
  cursor-agent -p --model "$slug" --mode plan --trust \
    --workspace "$dir" "<judge role card + VERDICT contract>"; } \
  > "$tmp/vote-$n.md" 2> "$tmp/vote-$n.err" &
# after `wait`:  mv "$tmp"/vote-*.md "$dir/50-votes/"
```

- `--trust` is mandatory headless — without it: exit 1 + "Workspace Trust Required",
  zero model output. Never pin `--sandbox` (fatal on this machine); `--mode plan`
  already carries read-only. The judge reads the assembled bundle from `--workspace`.
- `ensemble.py` consumes the leading `BACKEND:`/`MODEL:`/`SLOT:` header lines
  (`MODEL:` → `judge_mix` + the `Panel:` line; `SLOT:` informational).
- Flow unchanged: slots 1–3 → `ensemble.py tally --n-target 3` → on `escalate`
  spawn slots 4–5 → re-run `--n-target 5`. `decide()` mechanics are as shipped.
- Prompt = the existing judge role card + a bundle-only / no-web / no-other-files
  clause + the exact `VERDICT:` last-line contract.

Retry ladder (precedence pinned; each judge's exit code + stderr also append to
`$dir/50-votes/judge-errors.log`; no mid-run asks):

| Signal (see `.err`) | Action |
|---|---|
| Nonzero exit / empty stdout (slug rejected, trust, auth) | retry once → substitute `--model auto`, disclose `SUBSTITUTED(<slug>)` in the MODEL header |
| Exit 0 but unparseable vote | respawn same slug once → drop + disclose N (existing rule) |
| < 3 valid votes | NO-CALL (existing rule) |

### Full stage mapping (host = cursor)

| Claude Code facility | Cursor mapping |
|---|---|
| Run dir | ABSOLUTE root pinned: `~/.claude/skills/trading-research/runs/<TICKER>-<date>-<hhmm>/` — a foreign-cwd Cursor session must never drop `15-position.*` into an ungitignored project tree. |
| Agent tool: analysts ×3 ∥, debate 2 waves, risk officer | `cursor-agent -p --model composer-2.5 --mode plan --trust --workspace "$dir"` per role card; analysts backgrounded + `wait`; debate waves sequential; same artifact read-sets as the Pipeline stage table. |
| Judges (Stage 5) | see Judge invocation above. |
| Writer | same pattern, `--model claude-opus-4-8-thinking-high`; the writer alone reads `15-position.*` (invariant 12 unchanged); orchestrator saves stdout to `60-report.md`. |
| QA loop | `qa_check.py` mechanical as-is; fix pass via `composer-2.5`; loop unchanged. |
| Artifact tool (Stage 8 deliverable) | does not exist on Cursor → render house HTML (`render_report.py`), save to `reports/single-ticker/<TICKER>/` + open the local file; footer notes `artifact: local-html`. |
| stock-market-pro skill, LunarCrush MCP (P6), Crypto.com MCP | unavailable on Cursor → straight to `DEGRADED`/`MISSING` per the existing data-gap rules; crypto tickers out of scope (R4). |
| AskUserQuestion | ask in chat. |
| Token-cost footer field | `cost: cursor-subscription (N/A)`. |
| Vendor CLIs, `TRADING_RESEARCH_LEDGER` env | unchanged — the CLIs are cwd-independent (absolute UPSTREAM path + `.env`); the orchestrating shell exports the env var. |

**Invariant 17 (Cursor host; additive to 1–16):** Cursor judges run plan-mode,
read-only, bundle-only — a vote citing facts absent from the judge bundle is
malformed.
