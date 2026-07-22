<!-- riskbox-block: inserted verbatim, do not edit -->
### Risk box (computed)
- Today's move: 3.51% [P1.chg_pct_1d] = 1.20× ATR14 (normal)
- ATR14: 12.81 USD [P2.atr14] (2.93% [P2.atr14_pct] of P1.last 435.15); adverse −1× = 422.34, −2× = 409.54
- 30d σ: 1.59% [P2.sigma30]
- Invalidation anchor: SMA50 405.30 [P2.sma50] −1× ATR14 = 392.49 (long) / +1× ATR14 = 418.11 (short)
- Context: NORMAL (today 1.20× ATR14 vs 1.5× threshold; context only, not a call)
<!-- riskbox-block: end -->

A one-R stop means the entire distance from entry to the box’s buffered long invalidation boundary is the risk unit. Quantity should equal the permitted dollar loss divided by that per-share distance. Widening the stop without reducing quantity would exceed one R. The NORMAL flag describes current conditions only; it does not support larger size.

DATA GAP: entry price, shares, account equity, loss budget, other holdings, and correlated sector or factor exposures. Dollar risk and portfolio concentration therefore cannot be calculated.

DATA GAP: next announced earnings date [P5.next_earnings]. The holding period’s overlap with earnings—and thus event exposure relative to the stop—cannot be assessed. The missing bear artifact also leaves downside challenge coverage incomplete.

KEY POINTS: • Adverse move: use the box’s scenarios as path-risk references. • Invalidation: the buffered long boundary defines one R. • Event risk: earnings timing is unknown.
