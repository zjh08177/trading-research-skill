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
| 1b Position | orchestrator via `scripts/vendors/snaptrade_account.py` (cross-broker; SnapTrade-only — `schwab_account.py` fallback is dormant post-sunset); current-day only | session | live tool | `15-position.md` + `.json` (WITHHELD from stages 2–5) |
| 1c Left-side signals (computed) | orchestrator via `scripts/vendors/tiingo_history.py` + `scripts/{stretch,percentile,volume_climax,move_cluster,move_base_rate,exhaustion}.py` (in that order — `exhaustion.py` reads P9 facts the first five already merged) | — | `10-datapack.json` + full price history | P9.* facts merged into `10-datapack.json`/`.md`; raw history at `11-history.json` |
| 2 Analysts ×4 | Agent tool, parallel | sonnet | full pack verbatim | `20-analyst-{fund,tech,sent,meanrev}.md` |
| 3a Bull | Agent tool | sonnet | pack + analyst briefs | `30-debate.md` (Bull case section) |
| 3b Bear | Agent tool, runs AFTER 3a completes | sonnet | pack + analyst briefs + 3a's bull section | `30-debate.md` (Bear case section, appended) |
| 4a Risk box (computed) | `scripts/risk_box.py` | — | `10-datapack.json` | `40-riskbox-block.md` (inserted into report VERBATIM) |
| 4b Risk officer (computed) | `scripts/render_risk.py` | — | `10-datapack.json` | `40-risk.md` (verbatim box + **templated** 1R-stop/event-risk/concentration; Feature 21 WS-A — deterministic, pack-only + position-blind, depends only on the pack so it may run parallel with the Stage-2 analysts) |
| 5 Ensemble | N judge agents, parallel, byte-identical inputs | opus | pack + briefs + debate + `40-risk.md` (leads with the verbatim risk box) + guarded track record | `50-votes/vote-{1..N}.md` |
| 5b Tally | `scripts/ensemble.py` | — | votes | `55-rating-block.md` (inserted into report VERBATIM) |
| 6b Mean-reversion block render | `scripts/render_meanrev.py` | — | `10-datapack.json` | `53-meanrev-block.md` (inserted into report VERBATIM) |
| 6 Report | writer agent | opus | all artifacts + template + `15-position.json` | `60-report.md` |
| 7 QA | `scripts/qa_check.py` + 1 sonnet prose pass | sonnet | report + `10-datapack.json` + `15-position.json` | `70-qa.txt` + `70-qa-prose.txt` |
| 7c Disclosure patch (computed) | `scripts/run_stats.py --patch 60-report.md`, runs AFTER both Stage 7 checks pass | — | run folder | patches `60-report.md`'s `{{agent_count}}`/`{{model_mix}}`/`{{wall_s}}`/`{{cost_usd}}` tokens in place (invariant 7) |
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

## Historical as-of replay

`/trading-research <TICKER> <YYYY-MM-DD>` or `/trading-research <TICKER>
<YYYY/MM/DD>` appends a date token to the normal single-ticker invocation. A
token equal to today resolves to the normal **live** pipeline (`mode=report`,
unchanged). A token strictly in the past resolves to **replay** mode
(`mode=replay`): the pipeline reconstructs the report as if run on that date,
using only information that would have been available then. A future date is
rejected.

**Source contract (point-in-time only, enforced by `scripts/replay.py`):**
- Price/technicals come from the first **settled** bar at or before the
  requested cutoff — never a live/last-trade quote. `P1.last` is forbidden in
  a replay pack; use `P1.price` (settled close).
- SEC/fundamentals facts are filtered by **filing date** — a fact filed after
  the cutoff is excluded even if it describes an earlier period.
- News/sentiment (Marketaux) is filtered by **published-before-cutoff**; a
  headline published after the cutoff never enters the pack.
- Options and any live position are entirely omitted: `P4.*`, `P8.*`, and all
  `H1.*` position facts are forbidden, and `15-position.json`/`.md` are never
  written for a replay run.
- The run folder gets a `00-scope.json`/`.md` recording `mode`,
  `requested_cutoff`, `effective_market_asof`, `entry_market_asof`, and
  `generated_at` — every downstream agent stage must load and trust this file
  before doing anything else.

**WebSearch is banned in replay mode.** The sentiment/analyst role must not
use WebSearch or any current web data; if Marketaux returns nothing, mark
`DATA GAP` rather than reaching for live information. The writer and QA
stages must not reference or read `15-position.json` (it does not exist for
a replay run). QA runs in replay-aware mode:
`qa_check.py --replay --asof-cutoff <requested_cutoff> 60-report.md 10-datapack.json`
(no position-pack argument).

**Report banner.** Every replay report opens with a banner stating this is a
**Historical replay**, followed by the requested cutoff, the effective market
as-of, the entry market as-of, and the `generated_at` timestamp — all four
fields, before the executive summary.

Replay reports publish to **`reports/replay/<TICKER>/`** (never
`reports/single-ticker/<TICKER>/`) and append to a separate replay ledger
sidecar (`scripts/batch/publish_replay.py`, `ledger.py append --replay`) —
mechanically isolated from the live ledger and live report tree.

## Usage capture + evolve mode (v2.4a Flywheel)

Every run that reaches Stage 1 writes a metadata-only local usage row. After
Stage 0 writes `00-scope.md`, run the helper with the skill venv and `eval` its
export so terminal events reuse the same id:

```bash
usage_export=$("<SKILL_DIR>/.venv/bin/python" "<SKILL_DIR>/scripts/usage.py" start \
  --mode report --ticker "$TICKER" --job-tier "$JOB_TIER" \
  --asset-class "$ASSET_CLASS" --run-id "$RUN_ID" --run-dir "$RUN_DIR")
eval "$usage_export"
```

At the terminal boundary, run `<SKILL_DIR>/scripts/usage.py end` with the same
`TRADING_RESEARCH_INVOCATION_ID`, `--run-id`, `--run-dir`, and each produced
`--report-path`. On an abort after scope, run `<SKILL_DIR>/scripts/usage.py fail`
with the nonzero `--exit-code`. `--options-only` is terminal after
`render_options.py` succeeds; batch/portfolio children are started by
`scripts/batch/build_datapack.py` and ended by `workflows/portfolio_pipeline.js`.
The sink is `${XDG_DATA_HOME:-~/.local/share}/trading-research/usage/invocations.jsonl`
or `TRADING_RESEARCH_USAGE_LEDGER`; it is local `0600`, uncommitted, and must
not contain holdings/qty/cost amounts. `position_aware` is a boolean only.

`--evolve` is a standalone, non-ticker, read-only mode: record
`scripts/usage.py start --mode=evolve`, run `<SKILL_DIR>/scripts/evolve.py`
to emit `10-corpus-index.json`, `20-signals.json`, and `30-retro.md`, then record
`scripts/usage.py end`. It fetches no vendors, calls no agents, appends no ledger
row, and edits no skill files. It excludes prior `mode=evolve` rows from its own
corpus and labels calibration dormant until the resolved sidecar exists.

## Invariants

Enforce all eighteen (rows 1–17 plus 19; the Cursor-host addendum below is a
separate, additive "Invariant 18"). Any violation is a defect, not a judgment
call.

| # | Rule | Enforced by |
|---|---|---|
| 1 | No judgment single-samples: headline rating comes only from `ensemble.py` over N≥3 votes. | script emits `55-rating-block.md`; writer inserts verbatim; alteration = QA defect |
| 2 | Judges receive byte-identical inputs; orchestrator never summarizes, paraphrases, or "repairs" any agent output. The bear advocate runs AFTER the bull (3a → 3b, not parallel) and reads the bull's actual output — any bull claim the bear quotes or characterizes must be faithful to that text, never a fabricated strawman. | SKILL.md invariant + artifacts are the only inter-stage channel; `qa_check.py --debate 30-debate.md` hard-fails a quote attributed to the bull that doesn't appear in the Bull case section |
| 3 | Ledger is read/written only through `ledger.py`; reads filter `date_utc < as_of` (two-sided guard). | script is sole entry point; the `~/.tradingagents/memory/` path is BANNED |
| 4 | Numbers in the report carry `[P#.fact]` tags or a URL; untagged numbers fail QA. | `qa_check.py` vs `datapack.json` |
| 5 | Dead data source → named `MISSING(reason)` section + Data Gaps box; dead P1 → abstain report + `no_call` ledger row. Never silent. A DATA GAP/MISSING claim naming a fact the pack actually has a value for is a hallucination, not a legitimate gap. | pack contract + failure map; `qa_check.py --brief`/report scan hard-fails a hallucinated gap |
| 6 | Move/level language states ATR14 multiples before any escalation word. | prompts.md role cards + QA prose pass |
| 7 | Footer discloses: actual N, agent count, model mix, wall clock, token cost. Thin ensemble (<3 valid votes) is never presented as N≥3. | `scripts/run_stats.py --patch` (Stage 7c) computes and fills all 4 non-N fields mechanically from the artifacts actually present — the writer leaves them as literal unfilled tokens, never hand-counts; the post-patch `qa_check.py --check-footer` pass hard-fails an unfilled `{{...}}` token or a bare "not recorded" left in the Disclosure section |
| 8 | Escalation (spread ≥2 at N=3 → N=5) always runs; R7 overrun is disclosed, never skipped. | `ensemble.py` decision output |
| 9 | Spread ≥3 at N=5 → headline `NO-CALL`, distribution still published + ledger-logged. | `ensemble.py` |
| 10 | P1–P5 fill from the named vendor CLIs; every fallback-filled fact stamps its real `src` and the section is boxed `DEGRADED(P#, reason)` in Data Gaps. P1 carries a tiingo cross-check stamp: same-asof-date closes within 0.5% → CROSS-CHECK OK, else CROSS-CHECK FAIL named in Data Gaps (run continues). This settled-close check stays 2-source (UW settled bars + tiingo) — Finnhub's `/quote` has no as-of param, so it can only vote on the LIVE cross-check (invariant 11), not a settled historical close. | CLIs exit nonzero and never fabricate; orchestrator stamps src + gaps |
| 11 | Current-day runs use `uw_quote.py` `P1.last` (real trade-time) as the price headline; box it `DELAYED` when `P1.is_realtime` is false and `STALE(as-of <date>)` when its trade-date precedes `as_of`. `uw_quote` derives `P1.is_realtime` from `tape_time` freshness (UW REST verified real-time intraday — tape ~2-3s behind, matches Schwab NBBO/Tiingo IEX to <0.05%); a stale or after-hours tape reports false so the headline boxes `DELAYED`. Prior close and chg% derive from `uw_bars.py` settled bars, never the quote. The live quote is valid only when `as_of` is today — the CLI refuses a past/future `as_of` (exit 3), so back-dated runs use settled bars. When `P1.last` is absent (back-dated or quote-failed), the headline cites `[P1.price]` `settled close` — never a tag missing from the pack. On a live-price CROSS-CHECK FAIL (`P1.last` vs `P1.px_last_oob` beyond 0.5%), fetch `vendors/finnhub_oracle.py` (current-day only, `FINNHUB_API_KEY`) as a 3rd independent source and run `scripts/price_crosscheck.py` for a deterministic 2-of-3 resolution — never leave a live disagreement unresolved for judges to adjudicate through; an unresolvable 3-way split is disclosed as an open discrepancy fact (`P1.crosscheck_status=fail_3way`), never silently picked. | `uw_quote.py` guard (as_of==today, parsed dates); writer picks `[P1.last]`/`[P1.price]` by pack presence; `tiingo_oracle.py --live` `P1.px_last_oob` cross-checks; `price_crosscheck.py` resolves a disagreement 2-of-3 with `finnhub_oracle.py`'s vote |
| 12 | Position facts (`15-position.*`) are withheld from analysts, debate, risk, and judges; only the writer and `qa_check.py` read them. The rating is position-blind. | stage read-sets above; artifact is never merged into `10-datapack.*` |
| 13 | Account access is read-only across every position source: the CLIs list accounts + positions only (SnapTrade `list_user_accounts` + `get_all_account_positions`; the dormant `schwab_account.py` uses Schwab `GET /accounts`). No order, trade, or mutation endpoint is ever referenced. | CLIs hold no order path; `test_schwab_account.py` + `test_snaptrade_account.py` assert absence |
| 14 | Position is live-only: a past/future `--asof` yields no position (exit 3), never a fabricated historical holding. | `snaptrade_account.py` (active) / `schwab_account.py` (dormant) `--asof` guard (parsed dates vs today) |
| 15 | The position never changes the headline call — only the action framing around it. A "Your Position" section that argues the rating is a defect. | writer role card; QA prose pass |
| 16 | The risk box's adverse-move, invalidation, and context numbers come only from `risk_box.py`, inserted verbatim as `40-riskbox-block.md`. The whole risk officer artifact `40-risk.md` (verbatim box + templated 1R/event/concentration narration) is now produced deterministically by `render_risk.py` from the pack ALONE (Feature 21 WS-A): no LLM, no recompute, position-blind (invariant 12). The block is context-only — never an action/size, never changes the rating. | `risk_box.py` + `render_risk.py` emit the artifacts; `40-risk.md` is byte-equal to `render_risk.py` output by construction; `qa_check.py` exempts the verbatim region |
| 17 | Decision levels preserve execution qualifiers. Reports emit schema-v2 `LEVELS_JSON` with comparison, action strength, rating gate, and confirmation conditions. A crossed level is never automatically an execution instruction: Hold-rated directional triggers are review-only, legacy `LEVELS:` is upgraded to safe review-first semantics, and action-plan rows use `ACT` only for `confirmed_act`. | `levels_schema.py`; `render_report.py`; `monitor_invalidations.py`; `action_plan.py`; `portfolio_delta.py`; `qa_check.py --strict` |
| 19 | A counter-trend trigger (side opposing the dominant direction — a downside-Buy or upside-Sell/Trim) is never `action_strength="act"`, for any rating. A leveraged product's (`P0.leverage_objective` present) counter-trend trigger always carries a computed `decay_risk`. A `base_rate_cite` never appears without its `n_raw`/`n_regimes`/`n_macro` companions. | `levels_schema.validate_level_set()`; hard-fails `qa_check.py` and `render_report.py` (never a warning, never convention-only) |

## Byte-identical inputs and no paraphrase

Assemble the judge input once (pack + analyst briefs + debate + risk + guarded
track record) and pass the same bytes to every judge. Never summarize,
paraphrase, re-order, or "clean up" an agent's output — route it as-is through
the run-folder artifact. When a stage produces nothing, record the gap; never
invent its content.

## Debate structure: 2 waves by design, not a gap (reviewed 2026-07-17)

The bull writes once, blind; the bear writes once, with full visibility into
the bull's case and an offensive mandate; the bull never gets a rebuttal.
This is asymmetric by design — evaluated (advisor review, 2026-07-17) and
kept at 2 waves rather than adding a bull-rebuttal 3rd wave, because there is
no measured evidence of order-bias in this pipeline's ratings and the
mitigation is cheaper done at the judge layer (judge card's DEBATE FORMAT
NOTE: judges are told the asymmetry is structural, not evidence of
concession, and must check whether a persuasive bear attack targets a claim
the bull already tagged/falsified vs. one it left untagged).

**Escalate to a bounded 3rd wave (bull-rebuttal-only, terminates by rule —
the moving party gets one reply, the responding party gets none, never a
4th wave) only if:**
- a swap-order test (re-judge ~10-20 archived byte-identical bundles with
  bull/bear order swapped, or with the bull's tagged levels restated after
  the bear section) shows mean rating shift ≥1 notch or conviction shift
  ≥1.5 points attributable to order, OR
- judges are observed citing bear rebuttals as dispositive while ignoring
  that the bull's tagged levels already addressed them (i.e., the judge
  card's format-note instruction is demonstrably not being followed).

If the track record ledger later shows systematic bearish miscalibration
that the swap test can't explain by order, that points at the bear's
offensive mandate or judge priors — a different investigation, not this one.

## Data pack

Fetch the pack first (Stage 1) and inject it verbatim into every downstream
agent. Key `10-datapack.json` by fact id — `{"P2.atr14": {"v": 19.86, "unit":
"USD", "asof": "...", "src": "uw"}}` — and render the same facts
in `10-datapack.md`. Flag any section past its staleness threshold as `STALE`.

| § | Content | Source | STALE when |
|---|---|---|---|
| P1 | live price + day range/vol, chg%, 52wk, mcap (derived: price × EDGAR shares), avg vol | `vendors/uw_quote.py` (live `P1.last`) + `vendors/uw_bars.py` (settled close, chg%, 52wk) + `tiingo_oracle.py --live` cross-check; `vendors/finnhub_oracle.py` as a 3rd vote ONLY on a uw/tiingo live disagreement (see invariant 11); fallback stock-market-pro; crypto: Crypto.com MCP | quote trade-date < `as_of`; crypto >15 min |
| P2 | SMA20/50/200, RSI14, MACD, ATR14 (abs+%), 30d σ | `vendors/uw_bars.py`; fallback stock-market-pro | same as P1 |
| P3 | rev/EPS TTM+YoY, margins, FCF, net debt, P/E (derived), beta | `vendors/edgar_fundamentals.py` (core) + `vendors/uw_info.py` → `uw.fundamental` distiller (P3.beta only; UW has no like-for-like short-int-to-float/PEG/dividends post-sunset); fallback stock-market-pro; crypto: `MISSING(by-design)` | >100 days |
| P4 | ATM IV + term slope, put/call vol+OI, notable OI | UW P8 (`--options`, see below); no light source after the Schwab sunset — a plain run emits `P4 MISSING`; crypto: N/A | >1 trading day |
| P5 | ≤10 dated headlines + next earnings date | `vendors/marketaux_news.py` + WebSearch (earnings date); fallback stock-market-pro news | headline >14d dropped; event job >48h flagged |
| P6 | sentiment (equity: news tone; crypto: LunarCrush) | LunarCrush MCP / derived | >1 day |
| P7 | track record | `ledger.py read --ticker X --before <as_of>` | guard is code, not prose |
| P8 | dealer GEX + gamma regime/flip, IV rank/skew/term, max pain, OI walls, live flow + scored smart-money flow (`--options` only) | `vendors/uw_options.py` (Unusual Whales); suppresses P4 on success | per-fact daily/snapshot; live facts session-gated |
| P9 | left-side/right-side stretch (ATR+sigma multiples), RSI percentile (all + comparable-move-conditioned), volume climax, regime cluster status, forward-return base rate (raw/regime/macro sample sizes), exhaustion-turning-condition booleans + k/4 tally | `stretch.py` + `percentile.py` + `volume_climax.py` + `move_cluster.py` + `move_base_rate.py` + `exhaustion.py` (full history via `tiingo_history.py`) | stale if `10-datapack.json` P1/P2 sections are stale |

Vendor CLIs: run `<SKILL_DIR>/.venv/bin/python scripts/vendors/<cli>.py --ticker X --asof <date>`
(SKILL_DIR = this skill's repo root; bootstrap the venv once with `scripts/setup_venv.sh`).
Each prints one JSON object of facts on
stdout or exits nonzero with a one-line stderr reason — merge stdout objects into
`10-datapack.json` as-is. Nonzero exit → retry once → fall back per the table and
stamp `DEGRADED(P#, reason)`. List-valued facts (P4.iv_term, P4.notable_oi,
P5.headlines) are context, never numerically tagged in the report.

For P1 on a current-day run, also run `uw_quote.py` (live `P1.last` + day
range/vol; refuses a past `--asof` so back-dated runs use settled bars) and add
`--live` to `tiingo_oracle.py` (emits the `P1.px_last_oob` cross-check). Use
`P1.last` as the price headline with its trade-time as-of; keep `P1.price`
(settled) as the prior-close/chg% base per invariant 11. Compare `P1.last` to
`P1.px_last_oob`: if they agree within 0.5%, done. If they disagree, run
`vendors/finnhub_oracle.py --ticker <T> --asof <today>` for a 3rd vote, merge
its `P1.px_finnhub_oob` fact, then run `scripts/price_crosscheck.py
10-datapack.json` and merge its `P1.crosscheck_*` facts — this resolves a
2-of-3 majority deterministically or discloses an unresolvable 3-way split,
never leaves a bare CROSS-CHECK FAIL for judges to work around.

## Position (Stage 1b, current-day only)

Run `<SKILL_DIR>/.venv/bin/python scripts/vendors/snaptrade_account.py --ticker X --asof <date>`
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

**No live fallback (Schwab sunset):** if `snaptrade_account.py` exits 2
(unconfigured/auth), "Your Position" is omitted and noted in Data Gaps — the run
continues position-blind. The former `schwab_account.py` fallback is dormant (kept
in-repo, never auto-invoked) so the Schwab OAuth token can lapse without breaking a
run; SnapTrade already aggregates Schwab positions if the owner linked that broker.

When Stage 1b wrote the artifact, pass it to QA as `qa_check.py 60-report.md 10-datapack.json 15-position.json`; otherwise (back-dated/auth-fail runs write none) use the 2-arg form. `qa_check.py` tolerates an absent position path either way. Always append `--debate 30-debate.md` — this checks the bear's bull-attributed quotes against the actual `## Bull case` section (invariant 1, see below); a fabricated strawman quote is a hard QA failure independent of `--strict`. Always append `--brief 20-analyst-<name>.md` once per analyst artifact present — this catches a "DATA GAP"/MISSING claim naming a `[P#.fact]` the pack actually has a value for (a hallucination, hard-fail independent of `--strict`).

The prose-QA pass's raw response is the artifact — write it VERBATIM to
`70-qa-prose.txt` before proceeding to Stage 7b. Always append
`--prose-qa 70-qa-prose.txt` to the `qa_check.py` invocation — a missing or
empty file is a hard Stage-7 failure (the pass cannot be proven to have run
at all), same fail-closed posture as `qa_check.py`'s own exit code; stop
and re-run the prose pass rather than proceeding to 7b/8 without it. A
clean pass still writes the file — its content is the literal string
"PROSE QA: clean", never an empty file.

This first `qa_check.py` pass does NOT include `--check-footer` — the
Disclosure section's 4 tokens are correctly still unfilled at this point
(Stage 7c hasn't run yet), and `--check-footer` would hard-fail the
pipeline's own intentional pending state, making Stage 7c — gated on this
pass succeeding — impossible to ever reach.

**Stage 7c** (only after this first `qa_check.py` exits 0 AND the prose
pass is clean): run `scripts/run_stats.py <run_dir> --patch 60-report.md` —
this fills the Disclosure section's `{{agent_count}}`/`{{model_mix}}`/
`{{wall_s}}`/`{{cost_usd}}` tokens in place from the artifacts that now all
exist. Then re-run `qa_check.py` once more, same flags PLUS
`--check-footer`, to confirm the patched report both still passes
everything else and now has a fully populated footer — the patch only
replaces literal placeholder tokens, never touches tagged numbers, so this
should be a clean pass, not a new failure.

## Ledger

Read and write the ledger only through `scripts/ledger.py`. Set its path via the
`TRADING_RESEARCH_LEDGER` env var (vault `reports/ledger.jsonl`); there is no
default fallback. Fetch P7 with `ledger.py read --ticker <T> --before <as_of>`,
passing the run's `as_of` (the look-ahead guard). Append one row per run
(including aborts) with `ledger.py append`; on write failure it prints the row
with a MANUAL-APPEND banner and exits 2 — append that row by hand, never skip.

## Portfolio history (daily snapshot → delta, monitor SSOT)

Track the book's composition over time, zero-LLM. The daily runbook fetches holdings
ONCE and reuses it, so same-day artifacts can never disagree:

1. `scripts/batch/snapshot_holdings.py <reports>/portfolio/holdings-history` writes the
   day's snapshot (`YYYY-MM-DD.json`) — the single holdings SSOT. It subprocesses the
   SnapTrade holdings CLI under the skill venv (`SNAPTRADE_HOLDINGS_PY` /
   `SNAPTRADE_HOLDINGS_CLI` override the pinned defaults); vendor exit 2/3/4 pass through,
   a partial book writes + DEGRADEs and never same-day-downgrades.
2. Point the monitor and action-plan at that file instead of a fresh fetch:
   `monitor_invalidations.py <levels> <out_md> <asof> --holdings <snapshot>` and
   `action_plan.py ... <snapshot> ...` (both unwrap the envelope). Monitor rows carry
   a trigger `state`; the action plan may render `ACT` only when that state is
   `confirmed_act`. Crossed-but-unconfirmed or Hold-gated levels render `REVIEW`, and
   near levels render `WAIT`.
3. `scripts/batch/portfolio_delta.py <holdings-history> <ledger.jsonl> <reports>/portfolio
   <out_md>` diffs the two latest snapshots into adds/trims/exits and grades each against
   the ledger rating + fired monitor triggers (`monitor-<date>.json` sidecars). A
   Hold-rated add/trim/exit without a `confirmed_act` sidecar is a discipline issue,
   not a neutral "no-call".

**Position data never leaves disk (R4).** Snapshots, deltas, and activities are the live
book: they are git-ignored in the vault (`holdings-history/`, `activities/`) and are NEVER
published via the Artifact tool — that flow is for reports only.

## Risk box (computed)

Run `<SKILL_DIR>/.venv/bin/python scripts/risk_box.py 10-datapack.json > 40-riskbox-block.md`.
It computes the adverse move (ATR14 / 30d σ multiples), the today-move/ATR ratio, the
SMA50±ATR invalidation anchor, and a NORMAL/ABNORMAL context flag from pack facts. A
missing required fact → exit 3 (fail loud). Insert the block VERBATIM into the report's
`## Risk box` slot. The block is context-only: it never states an action or size and
never changes the rating (invariant 16).

**Feature 21 WS-A — the risk officer is now deterministic (no LLM).** Instead of spawning
a risk-officer agent, run `scripts/render_risk.py 10-datapack.json > 40-risk.md`. It emits
the complete `40-risk.md` — the verbatim box, then a **templated** narration (the 1R-stop
implication, the P5-earnings event-risk line, and a position-blind concentration principle),
all rule-derived; anything not rule-derivable is dropped, not paraphrased. It reads the pack
ALONE — never `15-position.json` (invariant 12: `40-risk.md` feeds the judge bundle and must
stay position-blind) — so it depends only on Stage 1 and may run parallel with the analysts.
`40-risk.md` is byte-equal to `render_risk.py` output. The old `## Risk officer` role card in
`references/prompts.md` is retired.

## Options analysis (`--options` / `--options-only`)

Two opt-in flags add an Unusual Whales dealer-positioning read — the **P8** pack.
Off by default (added latency + tokens); after the Schwab sunset there is NO light
options source, so a plain run emits `P4 MISSING` — options data requires
`--options` (UW P8).

**`--options` (add-on)** — single-ticker orchestrator flow:

1. **Stage 1**: after the P1/P2 pack is built, run `<SKILL_DIR>/.venv/bin/python
   scripts/vendors/uw_options.py --ticker X --spot <P1.last, fallback P1.price>
   --atr <P2.atr14> [--earnings <P5 date if resolved>]`. Merge every `P8.*` fact
   (scalars AND context lists) into `10-datapack.json` + a `## P8` section of
   `10-datapack.md`; route `P8._gaps` into Data gaps and keep it in the json so
   `render_options` echoes it in the block. P8 is the SOLE options source: a P8
   failure or a gapped P8 IV group is accepted as a named `P4`/`P8` gap — there is
   no Schwab IV backfill after the sunset.
   The flow-alerts fetch also emits `P8.smart_flow`: the top prints scored
   against an institutional ruleset (premium tier, DTE window, vol/OI,
   ask-side, sweep, opening, repeated) as a context list — describes
   positioning, never a strike/expiry to trade (O9/O10).
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

Options invariants (enforce alongside 1–17):

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
| O9 | Output describes positioning and levels; it NEVER **recommends** a strike or expiry to buy or sell. Observed flow may be shown descriptively (e.g. `P8.flow_alerts`/`P8.smart_flow` list a reported strike/expiry), but the report never prescribes a contract to trade. |
| O10 | `P8.smart_flow` is a deterministic score over the flow-alerts tape (premium/DTE/vol-OI/ask-side/sweep/opening/repeated); it drops prints below the $300k premium floor, is a context list (never number-tagged, O3), stamps `snapshot`, and never changes the rating (it informs positioning only, like all P8). |

## Ensemble tally

Spawn N=3 judges, write each vote to `50-votes/vote-<i>.md` ending in exactly one
`VERDICT: … | CONVICTION: … | ENTRY-PATH: … | WHY: …` line, then run `ensemble.py tally 50-votes
--n-target 3`. Act on the JSON decision on stderr: `escalate` → spawn 2 more
judges and re-run with `--n-target 5`; `no-call` → publish the distribution under
a NO-CALL headline; `publish` → done. Respawn a malformed vote once, then drop and
disclose it. Insert the emitted `55-rating-block.md` into the report verbatim.

The verdict line is **4-field and `ensemble.py` enforces it**: a vote missing
`ENTRY-PATH` is MALFORMED and silently discarded — it never degrades to a 3-field
parse. A whole panel emitting the stale 3-field form therefore yields `n_valid: 0`
and a NO-CALL on every ticker, with no error anywhere. The canonical judge card in
`references/prompts.md` is the source of truth for this contract; copy it rather
than re-typing the line. (Regression 2026-07-18: the batch pipeline carried the
3-field form and produced 32 straight NO-CALLs from 96 well-formed votes.)

## Failure map

| Failure | Behavior |
|---|---|
| One analyst/debater dies | proceed; role listed in Data Gaps; never re-invented by orchestrator |
| Analyst/debater output MALFORMED (`validate_artifact.py` fail) | respawn once → quarantine the raw text to `<artifact>-malformed.md` + `MISSING(reason)` in Data Gaps — same handling as a dead role, never fed downstream undisclosed |
| Judge malformed/hung | respawn once → drop + disclose N |
| P1 unfillable | abstain report + ledger row (`no_call`, `gaps:["price"]`) |
| Non-P1 section dead after 1 retry | `MISSING(reason)`, run continues |
| Vendor CLI nonzero exit | retry once → free-tool fallback; facts stamp real `src`; `DEGRADED(P#, reason)` in Data Gaps |
| Tiingo cross-check FAIL or unavailable | named in Data Gaps (`CROSS-CHECK FAIL/UNAVAILABLE`); run continues; never triggers P1 fallback alone |
| `snaptrade_account.py` exit 2 (unconfigured/auth) | "Your Position" omitted + noted in Data Gaps; run continues position-blind (the `schwab_account.py` fallback is dormant post-sunset — never auto-invoked) |
| Position CLI exit 3 (back-dated `--asof`) | no position section (expected for back-dated runs); run continues |
| Ticker not held (`H1.held=false`) | no position section; cold-start report unchanged |
| qa_check hard fail ×2 | ship with QA-exceptions box quoting failures verbatim; run `--strict` for release/blocking checks so warning-class numeric issues are hard failures |
| ledger append fails | print row in chat; never skip silently |
| Wall clock > 15 min | finish + disclose overrun (never abort for time) |
| Any P9 script (`stretch.py`/`percentile.py`/`volume_climax.py`/`move_cluster.py`/`move_base_rate.py`) exits nonzero | `MISSING(P9, reason)` in Data Gaps; Mean-Reversion analyst section notes DATA GAP; run continues |
| ENTRY-PATH missing/malformed on a judge vote | same malformed-vote handling as a missing VERDICT line (respawn once, then drop + disclose N) |

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
| Analysts, debate, risk officer, QA-fix | `gpt-5.5-medium` |
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
| Agent tool: analysts ×3 ∥, debate 2 waves, risk officer | `cursor-agent -p --model gpt-5.5-medium --mode plan --trust --workspace "$dir"` per role card; analysts backgrounded + `wait`; debate waves sequential; same artifact read-sets as the Pipeline stage table. |
| Judges (Stage 5) | see Judge invocation above. |
| Writer | same pattern, `--model claude-opus-4-8-thinking-high`; the writer alone reads `15-position.*` (invariant 12 unchanged); orchestrator saves stdout to `60-report.md`. |
| QA loop | `qa_check.py` mechanical as-is; fix pass via `gpt-5.5-medium`; loop unchanged. |
| Artifact tool (Stage 8 deliverable) | does not exist on Cursor → render house HTML (`render_report.py`), save to `reports/single-ticker/<TICKER>/` + open the local file; footer notes `artifact: local-html`. |
| stock-market-pro skill, LunarCrush MCP (P6), Crypto.com MCP | unavailable on Cursor → straight to `DEGRADED`/`MISSING` per the existing data-gap rules; crypto tickers out of scope (R4). |
| AskUserQuestion | ask in chat. |
| Token-cost footer field | `cost: cursor-subscription (N/A)`. |
| Vendor CLIs, `TRADING_RESEARCH_LEDGER` env | unchanged — the CLIs are cwd-independent (vendored closure + `~/.config/tradingagents` creds); the orchestrating shell exports the env var. |

**Invariant 18 (Cursor host; additive to 1–17):** Cursor judges run plan-mode,
read-only, bundle-only — a vote citing facts absent from the judge bundle is
malformed.

### Full stage mapping (host = codex)

ADDITIVE, same posture as the Cursor mapping. On **host = codex** the pipeline
does NOT run as orchestrator turns: `scripts/pipeline_driver.py` owns Stages
1–7c as ONE deterministic process, and every LLM worker inside it is a
`cursor-delegate.sh` subprocess (prompt on stdin, never argv; the driver writes
every artifact, so workers stay read-only). Codex's native `spawn_agent` is used
**zero** times for pipeline roles: a native subagent is a *model tool*, so every
spawn and every wait costs an orchestrator reasoning turn — the exact cost this
host exists to delete. Codex's own roster is OpenAI-only
(`~/.codex/models_cache.json`), so the cross-vendor judge panel and the opus
writer need the cursor CLI regardless.

**Install:** copy or symlink `hosts/codex-command.md` → `~/.codex/prompts/trading-research.md`.

**Scope (R4, same as Cursor):** single-ticker, **live mode only**. Batch/portfolio,
the daily invalidation monitor, crypto, `--options`/`--options-only`, and historical
as-of replay stay claude-code-only — the driver rejects each of them at parse time
with exit 2 rather than half-running them.

**Host detection:** the runner self-identifies (`TRADING_RESEARCH_HOST=codex`);
a runner that is not Codex ignores this subsection entirely.

The orchestrator's whole job is four steps.

| Step | Orchestrator action |
|---|---|
| **0 Scope** | Write `00-scope.md` (job class, ticker, asset class, as-of) into the run dir. Parse any model-routing request in the user's query into `routing.json` — name ONLY the slots the user changed; every unnamed slot keeps its default. Then run the absolute `usage.py start --mode report …` and `eval` its export (§Usage capture), exactly as the Cursor host does. |
| **1 Launch** | Start the driver as ONE background exec cell (below). Nothing else runs in that turn. |
| **2 Poll** | Re-read the driver's stdout log until the cell exits. **A poll carries NO analysis** — no summarizing heartbeats, no interpreting partial artifacts, no re-deriving what a stage "probably" concluded. Poll, see "still running", stop. |
| **3 Publish** | On exit 0, read `DRIVER-STATE.json`, report its summary, and run Stage 8 (below). On exit 10/20/2, handle the named `reason.code` — do not improvise past it. |

Run dir is the ABSOLUTE canonical path `<SKILL_DIR>/runs/<TICKER>-<asof>-<hhmm>`
(same R4 reason as the Cursor row: a foreign-cwd session must never drop
`15-position.*` into an ungitignored project tree). Use that exact path and pass
the matching `--stamp`, so Stage 1's `build_datapack.py` builds in place instead
of building elsewhere and copying — a mismatch makes the builder's `00-scope.md`
overwrite the one Stage 0 wrote.

```bash
# ONE background cell. Heartbeats go to the log; poll the log, not the workers.
"$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/pipeline_driver.py" \
  --ticker "$TICKER" --run-dir "$RUN_DIR" --routing "$RUN_DIR/routing.json" \
  --asof "$ASOF" --stamp "$STAMP" \
  --worker-wrapper ~/.agent/bin/cursor-delegate.sh \
  > "$RUN_DIR/driver.log" 2>&1
```

Heartbeat lines are `[driver] <iso-ts> stage=<n> status=<state> [extra]`, one per
state change. `--resume` re-stats the artifacts in order and restarts at the first
missing one (the existing resume rule, implemented natively).

**Driver exit codes — what the orchestrator does with each**

| Exit | Meaning | Orchestrator action |
|---|---|---|
| `0` | published-ready | Stage 8: copy `60-report.md` + `60-report.html` to `reports/single-ticker/<TICKER>/`, append `80-ledger-row.json` via `ledger.py append`, run `usage.py end`. Status may be `published-ready-with-qa-exceptions` — if so, SAY so; never report it as a clean pass. |
| `10` | needs-orchestrator | Read `DRIVER-STATE.json:reason` `{code, detail}` and act on the named code (e.g. `stage2-empty`, `prose-qa-missing`, `ensemble-undecodable`, `stage6-no-report`, `invariant-12-violation`, `script-failed:<name>`, `driver-crash`). Report the failure; re-run with `--resume` only after the named cause is actually fixed. |
| `20` | abstain | `p1-unfillable`: publish the abstain report and append the `no_call` ledger row (invariant 5 / failure map). |
| `2` | bad invocation | Out-of-scope flag, unreadable/malformed `routing.json`, unknown routing slot, non-absolute run dir, non-today `--asof`. Fix the invocation — this is never a data problem. |

The driver never copies to the vault and never appends to the canonical ledger
(`ledger_appended`/`vault_copied` are `false` in `DRIVER-STATE.json`). Stage 8
stays the orchestrator's gated step.

**Explicit prohibitions on this host** (each of these is what the audited
82-minute/328-request run actually did wrong):
- **Never `spawn_agent` for a pipeline role.** Analysts, debaters, risk officer,
  judges, writer, and QA prose pass are driver-owned worker subprocesses. A native
  subagent for any of them is a defect, not an optimization.
- **Never re-implement a stage inline.** If a stage failed, fix the cause and
  re-run with `--resume`; hand-running a role in the orchestrator turn breaks
  invariant 2 (the artifacts are the only inter-stage channel) and the receipt
  census that `run_stats.py` computes the disclosure footer from.
- **Never poll individual workers.** There is one thing to poll: the driver's log.

**Model slots.** Defaults are the cross-vendor table in §Model slots (Cursor host)
above, mirrored in the driver's `DEFAULT_ROUTING`. `routing.json` keys:
`analyst`, `bull`, `bear`, `risk`, `judges` (list of exactly 3),
`judges_escalation` (list of exactly 2), `writer`, `qa`. An unknown key or a
wrong-length judge list is exit 2, not a silently-ignored typo — a run that
quietly used the default panel when the user asked for another is an undisclosed
routing change.

**Artifact tool / HTML deliverable / cost footer**: identical to the Cursor rows —
no Artifact tool on Codex, so `render_report.py`'s local HTML plus the vault copy
IS the deliverable, the footer records `artifact: local-html`, and the cost field
is `cursor-subscription (N/A)`.

**Invariant 18 (extended — codex host; additive to 1–17):** in addition to the
bundle-only rule above, on this host (a) every worker runs in a **stage-scoped
view dir** holding only that stage's read-set per the Pipeline table, and no view
except the writer's may ever contain `15-position.*` — this makes invariant 12
mechanical instead of prompt-enforced, and the driver records every view dir plus
`position_views_ok` in `DRIVER-STATE.json`; and (b) a worker artifact is accepted
into the run folder only after `scripts/validate_artifact.py` passes for its role —
a rejected artifact is respawned once, then quarantined and disclosed per the
failure map, never fed downstream.

**TODO (UNVERIFIED — measure, do not assume): poll `yield_time_ms` ceiling.**
The intent is that step 2 polls with the LARGEST value Codex actually honors, so a
~15-minute driver run costs a handful of polls rather than dozens. It is unverified
whether Codex honors values above 30 s; until it is measured, poll at 30 s and do
not claim a larger interval works. Probe once in a live Codex Desktop session:
start a background cell `bash -lc 'sleep 600'`, then poll that same cell four times
requesting `yield_time_ms` = 10000, 30000, 60000, 120000, recording wall-clock
elapsed around each call. The honored ceiling is the largest requested value whose
observed gap matches the request within ~20%. Write the measured number here and
delete this TODO.

## Grounding and completion honesty

- **Self-grounding**: Before reporting any status or figure, audit each claim
  against a tool result from this session; if no tool result backs it, say so
  instead of asserting it.
- **Anti-early-stop**: End-of-turn self-check — if the last paragraph promises
  future work, either do the work now or explicitly hand off; never end on an
  unfulfilled promise.
- **Verifier cadence (hard cap)**: This pipeline's own gates — `qa_check.py`,
  `ensemble.py`, `run_stats.py --check-footer`, `validate_artifact.py` — ARE the
  verification layer for a single-ticker run; they are deterministic, they read
  the artifacts rather than a summary of them, and they already cover the three
  failure-prone edges. So for a single-ticker run, **do not spawn additional ad hoc
  audit/verifier subagents** — an extra reviewer here buys no coverage the gates
  do not already have and costs the orchestrator turns this pipeline is budgeted
  against. The ONE permitted extra verifier is a single fresh-context pass, and
  only when `qa_check.py --strict` has failed **twice in a row** on the same run
  (i.e. the existing QA-exceptions path is about to fire) — that is a real
  unexplained-failure signal, not a cadence.
