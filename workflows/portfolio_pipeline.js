export const meta = {
  name: 'portfolio-top10-v2',
  description: 'trading-research pipeline (analysts→debate→risk→N=5 opus ensemble→schema-v2 writer→QA) over an arbitrary holdings list',
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

async function usageTerminal(it, ok, reportPath, errText) {
  if (!it.invocation_id) return
  const runIdArg = it.run_id ? `--run-id ${it.run_id}` : ''
  // findings guardrail #3: build_datapack.py stamps mode=replay on the START row;
  // the pipeline must carry the SAME --mode through onto BOTH terminal calls (end
  // AND fail) so a replay run's terminal usage rows never silently fall back to
  // mode=report. Live items carry no `mode` field at all (byte-identical to today).
  const modeArg = it.mode ? `--mode ${it.mode}` : ''
  const common = `--invocation-id ${it.invocation_id} --ticker ${it.ticker} ${runIdArg} --run-dir ${it.run_dir} ${modeArg}`
  const cmd = ok
    ? `${SK}/.venv/bin/python ${SK}/scripts/usage.py end ${common} --report-path ${reportPath} --exit-code 0`
    : `${SK}/.venv/bin/python ${SK}/scripts/usage.py fail ${common} --exit-code 1`
  try {
    await agent(
      `Run this bash command, then reply with just "ok":\n${cmd}`,
      { phase: 'Usage', model: 'haiku', effort: 'low', label: `usage:${it.ticker}` })
  } catch (e) {
    log(`USAGE TERMINAL FAILED ${it.ticker}: ${String(e).slice(0, 160)}`)
  }
}

// findings guardrail #2 (00-scope.json contract): a replay item (mode==="replay",
// stamped by build_datapack.py --replay) MUST have a matching, parseable
// `<run_dir>/00-scope.json` BEFORE any agent stage runs — that file is the sole
// authority a downstream agent (sentiment analyst, writer, QA) can trust for
// mode/cutoff/as-of provenance. FAIL CLOSED: throw if it is missing, unparseable,
// or disagrees with the item on ticker/mode/requested_cutoff. Live items never
// write 00-scope.json (see build_datapack.py's live branch + its own test) — that
// absence is expected and MUST NOT fail the live path (Live behavior unchanged).
async function loadAndCheckScope(it) {
  const { ticker, run_dir: dir } = it
  const isReplay = it.mode === 'replay'
  const expectMode = it.mode || 'live'
  const expectCutoff = it.requested_cutoff || ''
  const scopePath = `${dir}/00-scope.json`
  const cmd = `${SK}/.venv/bin/python -c "
import json, os, sys
t, m, c, p, replay_required = sys.argv[1:6]
if not os.path.exists(p):
    if replay_required == '1':
        print(json.dumps({'valid': False, 'error': 'missing scope file: ' + p}))
        sys.exit(1)
    print(json.dumps({'valid': True, 'skipped': True}))
    sys.exit(0)
try:
    s = json.load(open(p))
except Exception as e:
    print(json.dumps({'valid': False, 'error': 'unparseable scope file ' + p + ': ' + str(e)}))
    sys.exit(1)
errs = []
if s.get('ticker') != t:
    errs.append('ticker item=' + t + ' scope=' + str(s.get('ticker')))
if s.get('mode', 'live') != m:
    errs.append('mode item=' + m + ' scope=' + str(s.get('mode')))
if c and s.get('requested_cutoff') != c:
    errs.append('requested_cutoff item=' + c + ' scope=' + str(s.get('requested_cutoff')))
if errs:
    print(json.dumps({'valid': False, 'error': '; '.join(errs)}))
    sys.exit(1)
print(json.dumps({'valid': True, 'generated_at': s.get('generated_at')}))
" "${ticker}" "${expectMode}" "${expectCutoff}" "${scopePath}" "${isReplay ? '1' : '0'}"`
  const result = await agent(
    `Run EXACTLY this bash command and return its stdout verbatim, nothing else:\n${cmd}`,
    { phase: 'Scope', model: 'haiku', effort: 'low', schema: {
      type: 'object', additionalProperties: true,
      properties: {
        valid: { type: 'boolean' }, error: { type: ['string', 'null'] },
        generated_at: { type: ['string', 'null'] }, skipped: { type: ['boolean', 'null'] },
      }, required: ['valid'],
    }, label: `scope:${ticker}` })
  if (!result || result.valid !== true) {
    throw new Error(`SCOPE_MISMATCH ${ticker}: ${(result && result.error) || '00-scope.json missing/unparseable/mismatched'}`)
  }
  return { isReplay, generatedAt: result.generated_at || null }
}

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
    // findings guardrail #2: load + fail-closed-validate 00-scope.json BEFORE any
    // agent stage runs. Gated on it.mode==='replay' inside loadAndCheckScope so the
    // live path (no 00-scope.json ever written) stays byte-identical to today.
    const scope = await loadAndCheckScope(it)
    const isReplay = scope.isReplay
    const sentimentMission = isReplay
      ? 'Mission: summarize the tape from P5 (dated headlines) + P6 (sentiment tone). Separate durable narrative from noise; weight recency; quote headline dates from P5. If P6 is DATA GAP, say so; do not infer sentiment from price. HISTORICAL REPLAY (as of ' + it.requested_cutoff + '): Do not use WebSearch or current web data; if Marketaux is empty, mark DATA GAP. Never surface post-cutoff information.'
      : 'Mission: summarize the tape from P5 (dated headlines) + P6 (sentiment tone). Separate durable narrative from noise; weight recency; quote headline dates from P5. If P6 is DATA GAP, say so; do not infer sentiment from price. If you have a WebSearch tool, you MAY add the next earnings date and 1-2 recent catalysts citing the URL on the same line, LABELED "(discovery, not point-in-time)"; if not, mark next-earnings DATA GAP.'
    // Stage 2 — analysts x3
    await parallel([
      () => analyst(dir, ticker, kind, 'Fundamental analyst', '20-analyst-fund.md',
        'Mission: judge business quality and valuation from P3 (financials) + P1 (quote/mcap): growth durability, margins, balance sheet, whether the multiple is supported. If P3 is MISSING (crypto/ETF/foreign-filer 20-F), say so once and reason from price/liquidity/positioning only.'),
      () => analyst(dir, ticker, kind, 'Technical analyst', '20-analyst-tech.md',
        'Mission: read trend, momentum, volatility from P2 (SMA/RSI/MACD/ATR/sigma) + P1. State where price sits vs SMA50/200 in ATR14 multiples. Every level/move in ATR14 units before any escalation word.'),
      () => analyst(dir, ticker, kind, 'Sentiment analyst', '20-analyst-sent.md', sentimentMission),
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
    // Stage 6 — writer (opus: schema-v2 decision levels + concrete position)
    const writerReadList = isReplay
      ? `10-datapack.md, 10-datapack.json, 20-analyst-fund.md, 20-analyst-tech.md, 20-analyst-sent.md, the four 30-debate-*.md, 40-risk.md, 55-rating-block.md, ${dir}/00-scope.json`
      : `10-datapack.md, 10-datapack.json, 20-analyst-fund.md, 20-analyst-tech.md, 20-analyst-sent.md, the four 30-debate-*.md, 40-risk.md, 55-rating-block.md, 15-position.json`
    const replayBanner = isReplay
      ? `\n\nHISTORICAL REPLAY BANNER — as the VERY FIRST lines of the report, before the executive summary, state plainly that this is a **Historical replay** and give all four fields: requested cutoff (${it.requested_cutoff}), effective market as-of (${it.effective_market_asof}), entry market as-of (${it.entry_market_asof}), generated_at (${scope.generatedAt}). Do NOT reference or read 15-position.json anywhere — it does not exist for a replay run. Do NOT include a "## Your position" section.`
      : ''
    await agent(
      `ROLE: Institutional report writer for ${ticker} (${kind}). Write ${dir}/60-report.md using the schema-v2 decision-level spec. Read the template ${SK}/references/report-template.md and ALL run artifacts in ${dir}/: ${writerReadList}.${replayBanner}

RULES (grounding):
- Insert ${dir}/55-rating-block.md VERBATIM into the rating slot — the headline rating comes ONLY from it; do not edit a character.
- Every number carries its [P#.fact] tag or a same-line source URL. Interpret; do not restate the pack. Moves in ATR14 units. Fill the Data Gaps box from every DATA GAP / MISSING marker. Never invent a number.
- Adjacency: a number written immediately before a [P#.fact] tag MUST equal that fact's pack value — never glue a threshold/comparison number (e.g. "RSI14 > 55") to an unrelated tag.
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
4. ${isReplay
        ? 'This is a HISTORICAL REPLAY run: omit the "## Your position" section entirely. Do not reference, read, or infer 15-position.json — it was never generated for this run.'
        : 'CONCRETE "## Your position" (only if 15-position.json H1.held=true; else omit). Position-blind, FINAL rating (invariant 15) — the position never argues it. State weight [H1.pct_of_book], shares [H1.shares], value, open P/L [H1.unrealized_pl_pct]. Then:\n   - SIZE band tied to rating: Sell → "trim ~25–40% (≈\\$X–Y off)" (\\$ from [H1.market_value]); Hold → "hold current size — add only above the upside trigger, exit below the downside"; Buy → "add ~X%".\n   - TWO-SIDED PLAN in \\$: "▼ below <downside \\$> → <sell/exit/trim>; ▲ above <upside \\$> → <add/buy>", each with % from spot and ATR distance.\n   - TAX flag from open-P/L sign: gain → "trimming realizes a taxable gain"; loss → "loss is tax-harvestable — mind the 30-day wash-sale window if you\'d rebuy".\n   - BOOK FIT: weight vs book + concentration note (>5% single-name, or sector-cluster membership).'}
5. Disclosure footer: Actual N from the rating block; agents ~16 (3 sonnet analysts + 4 sonnet debaters + 1 sonnet risk + 5 opus judges + 1 opus writer); models sonnet(analysts/debate/risk)+opus(judges/writer); wall/cost "batch-level"; "Not financial advice."\nWrite the finished report to ${dir}/60-report.md. Return the executive-summary paragraph only.`,
      { phase: 'Writer', model: 'opus', effort: 'high', label: `writer:${ticker}` })
    // Stage 7 — QA cite-check + prose pass, one fix if hard-fail
    const qaCmd = isReplay
      ? `python3 ${SK}/scripts/qa_check.py --replay --asof-cutoff ${it.requested_cutoff} ${dir}/60-report.md ${dir}/10-datapack.json`
      : `python3 ${SK}/scripts/qa_check.py ${dir}/60-report.md ${dir}/10-datapack.json ${dir}/15-position.json`
    let qa = await agent(
      `Run the cite-check: ${qaCmd}  (exit 0 clean, 1 on tagged mismatch). Capture its full output.\nThen do a PROSE pass: Read ${dir}/60-report.md and flag untagged numeric claims, paraphrase drift, escalation words used before an ATR14-normalized move, or an altered rating block. You verify prose, not arithmetic; do not rewrite.\nReturn JSON.`,
      { phase: 'QA', model: 'sonnet', effort: 'medium', schema: {
        type: 'object', additionalProperties: true,
        properties: {
          cite_exit: { type: 'number' },
          hard_fails: { type: 'array', items: { type: 'string' } },
          prose_exceptions: { type: 'array', items: { type: 'string' } },
        }, required: ['cite_exit', 'hard_fails', 'prose_exceptions'],
      }, label: `qa:${ticker}` })
    if (qa && ((qa.cite_exit && qa.cite_exit !== 0) || (qa.hard_fails && qa.hard_fails.length))) {
      const fixReadList = isReplay ? `${dir}/60-report.md and ${dir}/10-datapack.json` : `${dir}/60-report.md and ${dir}/10-datapack.json and ${dir}/15-position.json`
      await agent(
        `The report ${dir}/60-report.md failed QA. Cite-check hard failures: ${JSON.stringify(qa.hard_fails || [])}. Prose exceptions: ${JSON.stringify(qa.prose_exceptions || [])}.\nRead ${fixReadList}. Fix ONLY the flagged issues: correct each tagged number to match the pack value (0.5% tol), tag or remove untagged numbers in judgment sections, and NEVER touch the verbatim rating block.${isReplay ? ' Do NOT reference or read 15-position.json — this is a historical replay run.' : ''} Re-Write the corrected report to ${dir}/60-report.md. Return "fixed".`,
        { phase: 'QA', model: 'opus', effort: 'high', label: `qafix:${ticker}` })
      qa = await agent(
        `Re-run: ${qaCmd} and return JSON of the result.`,
        { phase: 'QA', model: 'sonnet', effort: 'low', schema: {
          type: 'object', additionalProperties: true,
          properties: { cite_exit: { type: 'number' }, hard_fails: { type: 'array', items: { type: 'string' } } },
          required: ['cite_exit', 'hard_fails'],
        }, label: `qa2:${ticker}` })
    }
    await usageTerminal(it, true, `${dir}/60-report.md`, null)
    return { ticker, kind, run_dir: dir, decision: tally || null, qa: qa || null,
      report_path: `${dir}/60-report.md` }
  } catch (e) {
    log(`ERROR ${ticker}: ${String(e).slice(0, 200)}`)
    await usageTerminal(it, false, `${dir}/60-report.md`, e)
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
