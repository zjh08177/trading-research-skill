export const meta = {
  name: 'rewrite-writers-v24',
  description: 'Re-run the opus writer on existing runs with the schema-v2 prompt (concrete position + qualifier-preserving decision levels), reusing cached ratings',
  phases: [{ title: 'Writer' }],
}
const SK = '/Users/bytedance/.claude/skills/trading-research'
const items = (typeof args === 'string' ? JSON.parse(args) : args) // [{ticker,kind,run_dir}]

const writerPrompt = (dir, ticker, kind) => `ROLE: Institutional report writer for ${ticker} (${kind}). Rewrite ${dir}/60-report.md using the schema-v2 decision-level spec. Read the template ${SK}/references/report-template.md and ALL run artifacts in ${dir}/: 10-datapack.md, 10-datapack.json, 20-analyst-fund.md, 20-analyst-tech.md, 20-analyst-sent.md, the four 30-debate-*.md, 40-risk.md, 55-rating-block.md, 15-position.json.

RULES (unchanged grounding):
- Insert ${dir}/55-rating-block.md VERBATIM into the rating slot — the headline rating comes ONLY from it; do not edit a character.
- Every number carries its [P#.fact] tag or a same-line source URL. Interpret; do not restate the pack. Moves in ATR14 units. Fill the Data Gaps box from every DATA GAP / MISSING marker.
- Headline price: P1.last with "STALE: last trade <date>" when its trade-date precedes as-of (weekend/holiday); else [P1.price] settled close; crypto uses [P1.price] spot.

NEW in schema-v2 — do these:
1. DASHBOARD: a key-indicators panel + decision rail are auto-inserted at the top by the renderer. Do NOT hand-tabulate raw indicators; interpret them.
2. TWO-SIDED, QUALIFIER-PRESERVING INVALIDATION (resolves the Hold ambiguity — CRITICAL): a level is never a bare price. State BOTH boundaries with their resulting action, direction, comparison, and confirmation requirement:
   - a DOWNSIDE level below which the thesis breaks → Sell / Exit / Trim
   - an UPSIDE level above which it upgrades → Buy / Add
   A Hold carries two real, distinct levels, but directional changes under Hold are review-only until explicit confirmation is satisfied. A Sell's upside = short-invalidation (→ stop trimming / re-rate to Hold). Put both in the Risk box prose AND the position plan. Choose each from pack SMA20/50/200, day range, or 52wk — cite which.
3. LEVELS MARKER: at the very END of the "## Risk box" section, emit ONE fenced schema-v2 block; do not emit legacy LEVELS in new reports:
   LEVELS_JSON:
   \`\`\`json
   {"schema":2,"spot":<price>,"triggers":[{"side":"downside","level":<price>,"intended_action":"<Sell|Exit|Trim|Stop trimming / re-rate>","basis":"<cited basis + qualifier>","comparison":"<intraday_below|close_below>","action_strength":"<review|act>","rating_gate":"<none|hold_requires_review>","conditions":[]},{"side":"upside","level":<price>,"intended_action":"<Buy|Add|Stop trimming / re-rate>","basis":"<cited basis + qualifier>","comparison":"<intraday_above|close_above>","action_strength":"<review|act>","rating_gate":"<none|hold_requires_review>","conditions":[]}]}
   \`\`\`
   For Hold, set directional add/trim/exit triggers to action_strength="review" and rating_gate="hold_requires_review".
4. CONCRETE "## Your position" (only if 15-position.json H1.held=true; else omit). Position-blind, FINAL rating (invariant 15) — the position never argues it. State weight [H1.pct_of_book], shares [H1.shares], value, open P/L [H1.unrealized_pl_pct]. Then:
   - SIZE band tied to rating: Sell → "trim ~25–40% (≈\$X–Y off)" (\$ from [H1.market_value]); Hold → "hold current size — add only above the upside trigger, exit below the downside"; Buy → "add ~X%".
   - TWO-SIDED PLAN in \$: "▼ below <downside \$> → <sell/exit/trim>; ▲ above <upside \$> → <add/buy>", each with % from spot and ATR distance.
   - TAX flag from open-P/L sign: gain → "trimming realizes a taxable gain"; loss → "loss is tax-harvestable — mind the 30-day wash-sale window if you'd rebuy".
   - BOOK FIT: weight vs book + concentration note (>5% single-name, or sector-cluster membership).
5. Disclosure footer: Actual N from the rating block; agents ~16; models sonnet(analysts/debate/risk)+opus(judges/writer); wall/cost "batch-level"; "Not financial advice."

Write the finished report to ${dir}/60-report.md (overwrite). Return the "## Your position" section text only.`

const results = await parallel(items.map((it) => () =>
  agent(writerPrompt(it.run_dir, it.ticker, it.kind),
    { phase: 'Writer', model: 'opus', effort: 'high', label: `rewrite:${it.ticker}` })
    .then((r) => ({ ticker: it.ticker, ok: !!r }))
    .catch((e) => ({ ticker: it.ticker, ok: false, err: String(e).slice(0, 120) }))))
return results
