# {{TICKER}} — {{job_class}} research

> As-of: {{as_of}} · Price: {{price}} {{price_tag}} · {{freshness}} · Data pack: {{run_id}}
> Not financial advice. Decision support only; you decide and execute.

## Executive summary

Three to five sentences: the call, the core reason, the main risk, the trigger
that would change it. Every number tagged.

<!-- rating-block: inserted verbatim, do not edit -->
{{55-rating-block.md inserted here verbatim from ensemble.py — do not edit}}

## Your position

Only when 15-position.json has H1.held=true. State your weight [H1.pct_of_book]
and open P/L [H1.unrealized_pl_pct] (relative framing; absolute shares/$ stay in
the run artifact). Then the action the call implies for a holder (trim/add/hold/
exit), measured against the risk-box invalidation level. The rating above is
position-blind and final. Omit this whole section if flat or absent.

## Thesis

Three to five drivers, each a short tagged paragraph. Interpret the pack; do not
restate it.

## Steelmanned counter-case

The strongest opposing case, taken seriously — the invalidation levels and the
evidence that would flip the rating.

## Risk box

- Adverse move: in ATR14 [P2.atr14] and 30d σ [P2.sigma30] multiples.
- Invalidation level: {{level}} (— ATR from the 50-day mean).
- Concentration / event risk: next earnings {{earnings_date}} [P5.next_earnings].

## Valuation

| Metric | Value | Peer / history | Tag |
|---|---|---|---|
| ... | ... | ... | [P3.*] |

## Catalysts

Dated forward events (earnings, product, macro), each with a date and a tagged
or cited basis.

## Data gaps

Box every DATA GAP and MISSING(reason) marker. State what is missing and whether
it invalidates the call.

## QA exceptions

Only if qa_check.py or the prose pass hard-failed twice: quote each failing line
verbatim and the rule it breaks. Omit when QA is clean.

## Track record

Excerpt from `ledger.py read --ticker {{TICKER}} --before {{as_of}}`: prior
look-ahead-guarded calls. "No prior track record" if empty.

## Disclosure

Actual N: {{n_valid}} valid votes · Agents: {{agent_count}} · Models:
{{model_mix}} · Wall clock: {{wall_s}}s · Token cost: ${{cost_usd}}.
A thin ensemble (<3 valid votes) is disclosed here, never presented as N≥3.
Not financial advice.
