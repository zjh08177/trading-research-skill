export const meta = {
  name: 'portfolio-top10-v2',
  description: 'v2.3 trading-research pipeline (analysts→debate→risk→N=5 opus ensemble→v2.3 writer→QA) over an arbitrary holdings list',
  phases: [
    { title: 'Analysts', detail: '3 sonnet analysts per ticker' },
    { title: 'Debate', detail: 'bull/bear, 2 waves, sonnet' },
    { title: 'Risk', detail: 'risk officer + judge bundle' },
    { title: 'Judges', detail: '5 opus judges, byte-identical bundle' },
    { title: 'Tally', detail: 'ensemble.py rating block' },
    { title: 'Writer', detail: 'opus institutional report' },
    { title: 'QA', detail: 'cite-check + prose pass (+1 fix)' },
  ],
}

const SK = '/Users/bytedance/.claude/skills/trading-research'
const items = (typeof args === 'string' ? JSON.parse(args) : args) // [{ticker,kind,run_dir}]

const HOUSE = (dir) => `You are one agent in a trading-research pipeline. Ground EVERY claim in the DATA PACK at ${dir}/10-datapack.md (fact-id keyed as [P#.fact]). Read it FIRST with the Read tool.
- Cite every number with its [P#.fact] tag, or a source URL on the same line.
- DATA GAP rule: if a needed number is NOT in the pack, write "DATA GAP: <what>" and move on. Never estimate, interpolate, or recall a number from memory.
- ATR rule: state any price move/level as a multiple of ATR14 [P2.atr14] BEFORE any escalation word (breakdown/breakout/crash/exit). For crypto where P2 is partial, use what P2 facts exist and flag gaps.
- Adjacency rule: a number written immediately before a [P#.fact] tag must equal the pack value (0.5% tol; prefix ~ for rounded). Restate a fact in another form (%, $B) AWAY from the tag. List/date facts: cite the tag with NO adjacent number.
- Do NOT restate the pack; interpret it. End with a "KEY POINTS:" line of 2-3 bullets.`

const analyst = (dir, ticker, kind, role, file, mission) => agent(
  `${HOUSE(dir)}\n\nROLE: ${role}.\n${mission}\nOutput <= 250 words. Then use the Write tool to save your brief (brief text only, no preamble) to ${dir}/${file}. Return your brief text.`,
  { phase: 'Analysts', model: 'sonnet', effort: 'medium', label: `${role.split(' ')[0].toLowerCase()}:${ticker}` })

const debater = (dir, ticker, side, wave, file, extra) => agent(
  `${HOUSE(dir)}\n\nROLE: ${side} advocate (debate wave ${wave}).\nFirst Read: ${dir}/20-analyst-fund.md, ${dir}/20-analyst-tech.md, ${dir}/20-analyst-sent.md${extra ? ', ' + extra : ''}.\nMission: build the strongest evidence-based ${side === 'Bull' ? 'LONG case — make it falsifiable, name the levels/dates that would confirm' : 'SHORT/AVOID case and steelman the downside — attack the opposing side\'s weakest tagged claims, name invalidation levels'}. Moves in ATR14 units; no price targets without a cited basis. Output <= 300 words. Then Write your case to ${dir}/${file}. Return your case text.`,
  { phase: 'Debate', model: 'sonnet', effort: 'medium', label: `${side.toLowerCase()}${wave}:${ticker}` })

async function runTicker(it) {
  const { ticker, kind, run_dir: dir } = it
  try {
    // Stage 2 — analysts x3
    await parallel([
      () => analyst(dir, ticker, kind, 'Fundamental analyst', '20-analyst-fund.md',
        'Mission: judge business quality and valuation from P3 (financials) + P1 (quote/mcap): growth durability, margins, balance sheet, whether the multiple is supported. If P3 is MISSING (crypto/ETF/foreign-filer 20-F), say so once and reason from price/liquidity/positioning only.'),
      () => analyst(dir, ticker, kind, 'Technical analyst', '20-analyst-tech.md',
        'Mission: read trend, momentum, volatility from P2 (SMA/RSI/MACD/ATR/sigma) + P1. State where price sits vs SMA50/200 in ATR14 multiples. Every level/move in ATR14 units before any escalation word.'),
      () => analyst(dir, ticker, kind, 'Sentiment analyst', '20-analyst-sent.md',
        'Mission: summarize the tape from P5 (dated headlines) + P6 (sentiment tone). Separate durable narrative from noise; weight recency; quote headline dates from P5. If P6 is DATA GAP, say so; do not infer sentiment from price. If you have a WebSearch tool, you MAY add the next earnings date and 1-2 recent catalysts citing the URL on the same line, LABELED "(discovery, not point-in-time)"; if not, mark next-earnings DATA GAP.'),
    ])
    // Stage 3 — debate, 2 waves
    await parallel([
      () => debater(dir, ticker, 'Bull', 1, '30-debate-w1-bull.md', null),
      () => debater(dir, ticker, 'Bear', 1, '30-debate-w1-bear.md', null),
    ])
    await parallel([
      () => debater(dir, ticker, 'Bull', 2, '30-debate-w2-bull.md', `${dir}/30-debate-w1-bull.md, ${dir}/30-debate-w1-bear.md (rebut the bear directly)`),
      () => debater(dir, ticker, 'Bear', 2, '30-debate-w2-bear.md', `${dir}/30-debate-w1-bull.md, ${dir}/30-debate-w1-bear.md (rebut the bull directly)`),
    ])
    // Stage 4 — risk officer
    await agent(
      `${HOUSE(dir)}\n\nROLE: Risk officer. Size the RISK, not the view. First Read the pack and all four debate files (${dir}/30-debate-w1-bull.md, w1-bear, w2-bull, w2-bear).\nState position risk in ATR14 [P2.atr14] and 30d sigma [P2.sigma30] terms: plausible adverse move (in ATR multiples), the invalidation level, and what a 1R stop implies. Flag concentration and event risk (next earnings if known, else DATA GAP). Every move in ATR14 units BEFORE any escalation word. Output <= 250 words. End with "KEY POINTS: <adverse move, invalidation, event risk>". Then Write to ${dir}/40-risk.md. Return the text.`,
      { phase: 'Risk', model: 'sonnet', effort: 'medium', label: `risk:${ticker}` })
    // Assemble byte-identical judge bundle + votes dir (mechanical)
    await agent(
      `Run EXACTLY this bash command, then reply with just "ok":\nmkdir -p ${dir}/50-votes && { printf '# JUDGE BUNDLE — ${ticker}\\n'; for f in 10-datapack.md 20-analyst-fund.md 20-analyst-tech.md 20-analyst-sent.md 30-debate-w1-bull.md 30-debate-w1-bear.md 30-debate-w2-bull.md 30-debate-w2-bear.md 40-risk.md; do printf '\\n\\n===== %s =====\\n\\n' "$f"; cat "${dir}/$f" 2>/dev/null || printf '(missing %s)\\n' "$f"; done; } > ${dir}/45-judge-bundle.md`,
      { phase: 'Risk', model: 'haiku', effort: 'low', label: `bundle:${ticker}` })
    // Stage 5 — N=5 opus judges, byte-identical bundle
    const JUDGE = (i) => agent(
      `You are ONE of 5 independent PM adjudicators (judge #${i}) for ${ticker}. Do NOT reference the other judges. Read the byte-identical case bundle at ${dir}/45-judge-bundle.md (data pack + analyst briefs + bull/bear debate + risk box + P7 track record).\nAdjudicate independently: weigh business quality, technicals, tape, the risk box, and the guarded track record. Reason in <= 200 words citing tagged facts; state moves in ATR14 units.\nThen use the Write tool to save your FULL response (reasoning + final verdict line) to ${dir}/50-votes/vote-${i}.md. The LAST non-empty line MUST be EXACTLY, nothing after it:\nVERDICT: <StrongSell|Sell|Hold|Buy|StrongBuy> | CONVICTION: <1-10> | WHY: <one sentence>\nReturn just that final VERDICT line.`,
      { phase: 'Judges', model: 'opus', effort: 'high', label: `judge${i}:${ticker}` })
    await parallel([1, 2, 3, 4, 5].map((i) => () => JUDGE(i)))
    // Stage 5b — tally via ensemble.py (writes 55-rating-block.md + 55-decision.json to disk)
    const tally = await agent(
      `Run this bash, then return the decision JSON verbatim:\npython3 ${SK}/scripts/ensemble.py tally ${dir}/50-votes --n-target 5 > ${dir}/55-rating-block.md 2> ${dir}/55-decision.json; echo "RATING_BLOCK_BYTES=$(wc -c < ${dir}/55-rating-block.md)"; cat ${dir}/55-decision.json\nReturn ONLY the JSON object from 55-decision.json (the decision line).`,
      { phase: 'Tally', model: 'haiku', effort: 'low', schema: {
        type: 'object', additionalProperties: true,
        properties: {
          decision: { type: 'string' }, mode_label: { type: ['string', 'null'] },
          spread: { type: 'number' }, n_valid: { type: 'number' },
          mean_conviction: { type: 'number' }, malformed: { type: 'array' },
        }, required: ['decision', 'spread', 'n_valid'],
      }, label: `tally:${ticker}` })
    // Stage 6 — writer (opus, v2.3: two-sided LEVELS marker + concrete position)
    await agent(
      `ROLE: Institutional report writer for ${ticker} (${kind}). Write ${dir}/60-report.md using the v2.3 spec. Read the template ${SK}/references/report-template.md and ALL run artifacts in ${dir}/: 10-datapack.md, 10-datapack.json, 20-analyst-fund.md, 20-analyst-tech.md, 20-analyst-sent.md, the four 30-debate-*.md, 40-risk.md, 55-rating-block.md, 15-position.json.

RULES (grounding):
- Insert ${dir}/55-rating-block.md VERBATIM into the rating slot — the headline rating comes ONLY from it; do not edit a character.
- Every number carries its [P#.fact] tag or a same-line source URL. Interpret; do not restate the pack. Moves in ATR14 units. Fill the Data Gaps box from every DATA GAP / MISSING marker. Never invent a number.
- Adjacency: a number written immediately before a [P#.fact] tag MUST equal that fact's pack value — never glue a threshold/comparison number (e.g. "RSI14 > 55") to an unrelated tag.
- Headline price: P1.last with "STALE: last trade <date>" when its trade-date precedes as-of (weekend/holiday); else [P1.price] settled close; crypto uses [P1.price] spot.

NEW in v2.3 — do these:
1. DASHBOARD: a key-indicators panel + decision rail are auto-inserted at the top by the renderer. Do NOT hand-tabulate raw indicators; interpret them.
2. TWO-SIDED, ACTION-LABELED INVALIDATION (resolves the Hold ambiguity — CRITICAL): a level is never a bare price. State BOTH boundaries with their resulting action + direction:
   - a DOWNSIDE level below which the thesis breaks → Sell / Exit / Trim
   - an UPSIDE level above which it upgrades → Buy / Add
   A Hold carries two real, distinct levels (below→exit, above→add). A Sell's upside = short-invalidation (→ stop trimming / re-rate to Hold). Put both in the Risk box prose AND the position plan. Choose each from pack SMA20/50/200, day range, or 52wk — cite which.
3. LEVELS MARKER: at the very END of the "## Risk box" section, emit ONE line, exactly:
   LEVELS: downside=<price>|<Sell|Exit|Trim> upside=<price>|<Buy|Add|None> basis_dn=<SMA200|SMA50|SMA20|day-low> basis_up=<SMA50|SMA20|day-high|SMA200>
   (single-token actions/bases, no spaces inside a token; use real pack prices.)
4. CONCRETE "## Your position" (only if 15-position.json H1.held=true; else omit). Position-blind, FINAL rating (invariant 15) — the position never argues it. State weight [H1.pct_of_book], shares [H1.shares], value, open P/L [H1.unrealized_pl_pct]. Then:
   - SIZE band tied to rating: Sell → "trim ~25–40% (≈\$X–Y off)" (\$ from [H1.market_value]); Hold → "hold current size — add only above the upside trigger, exit below the downside"; Buy → "add ~X%".
   - TWO-SIDED PLAN in \$: "▼ below <downside \$> → <sell/exit/trim>; ▲ above <upside \$> → <add/buy>", each with % from spot and ATR distance.
   - TAX flag from open-P/L sign: gain → "trimming realizes a taxable gain"; loss → "loss is tax-harvestable — mind the 30-day wash-sale window if you'd rebuy".
   - BOOK FIT: weight vs book + concentration note (>5% single-name, or sector-cluster membership).
5. Disclosure footer: Actual N from the rating block; agents ~16 (3 sonnet analysts + 4 sonnet debaters + 1 sonnet risk + 5 opus judges + 1 opus writer); models sonnet(analysts/debate/risk)+opus(judges/writer); wall/cost "batch-level"; "Not financial advice."\nWrite the finished report to ${dir}/60-report.md. Return the executive-summary paragraph only.`,
      { phase: 'Writer', model: 'opus', effort: 'high', label: `writer:${ticker}` })
    // Stage 7 — QA cite-check + prose pass, one fix if hard-fail
    let qa = await agent(
      `Run the cite-check: python3 ${SK}/scripts/qa_check.py ${dir}/60-report.md ${dir}/10-datapack.json ${dir}/15-position.json  (exit 0 clean, 1 on tagged mismatch). Capture its full output.\nThen do a PROSE pass: Read ${dir}/60-report.md and flag untagged numeric claims, paraphrase drift, escalation words used before an ATR14-normalized move, or an altered rating block. You verify prose, not arithmetic; do not rewrite.\nReturn JSON.`,
      { phase: 'QA', model: 'sonnet', effort: 'medium', schema: {
        type: 'object', additionalProperties: true,
        properties: {
          cite_exit: { type: 'number' },
          hard_fails: { type: 'array', items: { type: 'string' } },
          prose_exceptions: { type: 'array', items: { type: 'string' } },
        }, required: ['cite_exit', 'hard_fails', 'prose_exceptions'],
      }, label: `qa:${ticker}` })
    if (qa && ((qa.cite_exit && qa.cite_exit !== 0) || (qa.hard_fails && qa.hard_fails.length))) {
      await agent(
        `The report ${dir}/60-report.md failed QA. Cite-check hard failures: ${JSON.stringify(qa.hard_fails || [])}. Prose exceptions: ${JSON.stringify(qa.prose_exceptions || [])}.\nRead ${dir}/60-report.md and ${dir}/10-datapack.json and ${dir}/15-position.json. Fix ONLY the flagged issues: correct each tagged number to match the pack value (0.5% tol), tag or remove untagged numbers in judgment sections, and NEVER touch the verbatim rating block. Re-Write the corrected report to ${dir}/60-report.md. Return "fixed".`,
        { phase: 'QA', model: 'opus', effort: 'high', label: `qafix:${ticker}` })
      qa = await agent(
        `Re-run: python3 ${SK}/scripts/qa_check.py ${dir}/60-report.md ${dir}/10-datapack.json ${dir}/15-position.json and return JSON of the result.`,
        { phase: 'QA', model: 'sonnet', effort: 'low', schema: {
          type: 'object', additionalProperties: true,
          properties: { cite_exit: { type: 'number' }, hard_fails: { type: 'array', items: { type: 'string' } } },
          required: ['cite_exit', 'hard_fails'],
        }, label: `qa2:${ticker}` })
    }
    return { ticker, kind, run_dir: dir, decision: tally || null, qa: qa || null,
      report_path: `${dir}/60-report.md` }
  } catch (e) {
    log(`ERROR ${ticker}: ${String(e).slice(0, 200)}`)
    return { ticker, kind, run_dir: dir, error: String(e).slice(0, 300) }
  }
}

log(`Starting v2 pipeline over ${items.length} holdings: ${items.map((i) => i.ticker).join(', ')}`)
const results = await parallel(items.map((it) => () => runTicker(it)))
const ok = results.filter(Boolean)
log(`Done. ${ok.filter((r) => !r.error).length}/${items.length} completed.`)
return ok.map((r) => ({ ticker: r.ticker, kind: r.kind,
  rating: r.decision ? (r.decision.mode_label || r.decision.decision) : (r.error ? 'ERROR' : '?'),
  decision: r.decision ? r.decision.decision : null,
  spread: r.decision ? r.decision.spread : null,
  n_valid: r.decision ? r.decision.n_valid : null,
  mean_conv: r.decision ? r.decision.mean_conviction : null,
  qa_exit: r.qa ? r.qa.cite_exit : null,
  qa_hard: r.qa && r.qa.hard_fails ? r.qa.hard_fails.length : null,
  report_path: r.report_path || null, error: r.error || null }))
