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

<!-- riskbox-block: inserted verbatim, do not edit -->
{{40-riskbox-block.md inserted here verbatim from risk_box.py — do not edit or recompute}}

The risk officer's narration (1R stop from the invalidation anchor, concentration,
and event risk: next earnings {{earnings_date}} [P5.next_earnings]) goes BELOW the
block. The computed block is context-only and never changes the rating.

At the end of this section, emit the schema-v2 machine-readable levels block.
It preserves comparison rules, rating gates, and confirmation conditions; Hold
directional triggers must be review-only.

LEVELS_JSON:
```json
{
  "schema": 2,
  "spot": {{price}},
  "triggers": [
    {
      "side": "downside",
      "level": {{downside_level}},
      "intended_action": "{{Sell|Exit|Trim|Stop trimming / re-rate}}",
      "basis": "{{cited basis and qualifier}}",
      "comparison": "{{intraday_below|close_below}}",
      "action_strength": "{{review|act}}",
      "rating_gate": "{{none|hold_requires_review}}",
      "conditions": []
    },
    {
      "side": "upside",
      "level": {{upside_level}},
      "intended_action": "{{Buy|Add|Stop trimming / re-rate}}",
      "basis": "{{cited basis and qualifier}}",
      "comparison": "{{intraday_above|close_above}}",
      "action_strength": "{{review|act}}",
      "rating_gate": "{{none|hold_requires_review}}",
      "conditions": []
    }
  ]
}
```

## Valuation

| Metric | Value | Peer / history | Tag |
|---|---|---|---|
| ... | ... | ... | [P3.*] |

## Catalysts

Dated forward events (earnings, product, macro), each with a date and a tagged
or cited basis.

## Dealer Positioning & Options

Only when the run used `--options`. The orchestrator computes this section into
`52-options-block.md` (render_options.py); insert it here VERBATIM under the
markers — do not edit, re-order, or recompute. Every fact carries its
daily/snapshot/live history tag; describe positioning and levels, never name a
strike/expiry to trade. Omit the whole section when the run did not fetch P8.

<!-- options-block: inserted verbatim, do not edit -->
{{52-options-block.md inserted here verbatim from render_options.py — do not edit}}

## Mean-Reversion / Exhaustion

<!-- writer: insert 53-meanrev-block.md VERBATIM here, unedited. Omit this
     whole section only if 53-meanrev-block.md does not exist (pipeline bug
     — this analyst is always-on, so its absence should be rare and must be
     named in Data Gaps, never silently dropped). -->

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
