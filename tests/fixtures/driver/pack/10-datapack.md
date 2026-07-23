# Data pack — UNH (equity), as-of 2026-07-21 (run UNH-2026-07-21-2147)

P1 cross-check: CROSS-CHECK OK (uw 436.35 vs tiingo 436.35, rel 0.0000% <= 0.5%)

Headline price note: last trade [P1.last]=435.15 is the 2026-07-21 session. Prior close/chg% from settled bars: 436.35 close.

## P0 What-changed
- [P0.move_pct] = 3.510852805123954 pct (asof 2026-07-21, src derived(uw_bars.chg_pct_1d))
- [P0.move_vs_atr] = 1.196 ATRs (asof 2026-07-21, src derived(uw_bars/atr14))
- [P0.rel_volume] = 1.078 x_avg (asof 2026-07-21, src derived(uw_quote/uw_bars))
- [P0.catalysts] = [{"title": "UnitedHealth's Turnaround Is Progressing Even Better Than I Thought (NYSE:UNH)", "source": "seekingalpha.com", "published_at": "2026-07-17T11:30:00.000000Z", "url": "https://seekingalpha.com/article/4922821-unitedhealth-stock-turnaround-progressing-better-than-i-thought-earnings-review", "sentiment": 0.3568}, {"title": "UnitedHealth Group Q2: The Real Test Begins Now (Downgrade) (NYSE:UNH)", "source": "seekingalpha.com", "published_at": "2026-07-16T20:35:06.000000Z", "url": "https://seekingalpha.com/article/4922769-unitedhealth-group-q2-the-real-test-begins-now-downgrade", "sentiment": 0.10115}, {"title": "UnitedHealth Group Incorporated (UNH) Q2 2026 Earnings Call Transcript", "source": "seekingalpha.com", "published_at": "2026-07-16T16:46:16.000000Z", "url": "https://seekingalpha.com/article/4922664-unitedhealth-group-incorporated-unh-q2-2026-earnings-call-transcript", "sentiment": 0.0}] articles (asof 2026-07-17T11:30:00.000000Z, src marketaux)

## P1 Quote
- [P1.price] = 436.35 USD (asof 2026-07-21, src uw)
- [P1.chg_pct_1d] = 3.510852805123954 pct (asof 2026-07-21, src uw)
- [P1.high_52w] = 461.62 USD (asof 2026-07-21, src uw)
- [P1.low_52w] = 234.6 USD (asof 2026-07-21, src uw)
- [P1.avg_vol_20d] = 6341315.05 shares (asof 2026-07-21, src uw)
- [P1.last] = 435.15 USD (asof 2026-07-21T23:55:07Z, src uw)
- [P1.day_high] = 437.02 USD (asof 2026-07-21T23:55:07Z, src uw)
- [P1.day_low] = 435.15 USD (asof 2026-07-21T23:55:07Z, src uw)
- [P1.day_volume] = 6833581.0 shares (asof 2026-07-21T23:55:07Z, src uw)
- [P1.is_realtime] = False bool (asof 2026-07-21T23:55:07Z, src uw)
- [P1.px_close_oob] = 436.35 USD (asof 2026-07-21, src tiingo)
- [P1.px_last_oob] = 436.35 USD (asof 2026-07-21T20:00:00+00:00, src tiingo)
- [P1.mcap] = 396268810685 USD (asof 2026-04-30, src derived(uw*sec-edgar))

## P2 Technicals
- [P2.sma20] = 422.7035000000001 USD (asof 2026-07-21, src uw)
- [P2.sma50] = 405.2997999999999 USD (asof 2026-07-21, src uw)
- [P2.sma200] = 342.23474999999996 USD (asof 2026-07-21, src uw)
- [P2.rsi14] = 62.3616391587923 index (asof 2026-07-21, src uw)
- [P2.atr14] = 12.80658731156944 USD (asof 2026-07-21, src uw)
- [P2.atr14_pct] = 2.934934642275568 pct (asof 2026-07-21, src uw)
- [P2.macd] = 7.351916046255894 USD (asof 2026-07-21, src uw)
- [P2.macd_signal] = 8.24635444972464 USD (asof 2026-07-21, src uw)
- [P2.sigma30] = 1.5915761158031443 pct (asof 2026-07-21, src uw)

## P3 Fundamentals (SEC XBRL)
- [P3.revenue_ttm] = 446073000000 USD (asof 2026-03-31, src sec-edgar)
- [P3.revenue_yoy] = 13.842922476979936 pct (asof 2026-03-31, src sec-edgar)
- [P3.eps_diluted_ttm] = 20.08 USD (asof 2026-03-31, src sec-edgar)
- [P3.eps_yoy] = 27.411167512690348 pct (asof 2026-03-31, src sec-edgar)
- [P3.operating_margin_ttm] = 6.181499440674509 pct (asof 2026-03-31, src sec-edgar)
- [P3.net_margin_ttm] = 4.108296175738052 pct (asof 2026-03-31, src sec-edgar)
- [P3.fcf_ttm] = 16075000000 USD (asof 2025-12-31, src sec-edgar)
- [P3.total_debt] = 88837000000 USD (asof 2026-03-31, src sec-edgar)
- [P3.cash_and_equivalents] = 28001000000 USD (asof 2026-03-31, src sec-edgar)
- [P3.net_debt] = 60836000000 USD (asof 2026-03-31, src sec-edgar)
- [P3.shares_outstanding] = 908144404 shares (asof 2026-04-30, src sec-edgar)
- [P3.latest_10k_filed] = 2026-03-02 date (asof 2026-02-20, src sec-edgar)
- [P3.latest_10q_filed] = 2026-05-05 date (asof 2026-04-30, src sec-edgar)
- [P3.pe_ttm] = 21.73 ratio (asof 2026-07-21, src derived(uw/edgar))
- [P3.beta] = 0.5379 index (asof 2026-07-21, src uw(info))

## P5 News/events
- [P5.headlines] = [{"title": "UnitedHealth's Turnaround Is Progressing Even Better Than I Thought (NYSE:UNH)", "source": "seekingalpha.com", "published_at": "2026-07-17T11:30:00.000000Z", "url": "https://seekingalpha.com/article/4922821-unitedhealth-stock-turnaround-progressing-better-than-i-thought-earnings-review", "sentiment": 0.3568}, {"title": "UnitedHealth Group Q2: The Real Test Begins Now (Downgrade) (NYSE:UNH)", "source": "seekingalpha.com", "published_at": "2026-07-16T20:35:06.000000Z", "url": "https://seekingalpha.com/article/4922769-unitedhealth-group-q2-the-real-test-begins-now-downgrade", "sentiment": 0.10115}, {"title": "UnitedHealth Group Incorporated (UNH) Q2 2026 Earnings Call Transcript", "source": "seekingalpha.com", "published_at": "2026-07-16T16:46:16.000000Z", "url": "https://seekingalpha.com/article/4922664-unitedhealth-group-incorporated-unh-q2-2026-earnings-call-transcript", "sentiment": 0.0}] articles (asof 2026-07-21, src marketaux)

## P6 Sentiment
- [P6.news_tone] = 0.1527 score[-1,1] (asof 2026-07-21, src derived(marketaux,n=3))
- [P6.reddit_crowding] = 3 mentions (asof 2026-07-21, src apewisdom)
- [P6.youtube_attention] = 50 videos (asof 2026-07-21, src youtube(search.list))
- [P6.social_risk] = none label (asof 2026-07-21, src derived(P6))

## P7 Track record
| Date | Rating | Spread | No-call | Report |
|---|---|---|---|---|
| 2026-07-05 | Hold | 0 | False | reports/UNH-2026-07-05.md |
| 2026-07-07 | NoCall | 0 | True | reports/single-ticker/UNH/UNH-2026-07-07.md |

## Data gaps
- P4 MISSING(no light options source after Schwab sunset; pass --options for the UW P8 pack)
- P5.next_earnings MISSING(not announced on UnitedHealth Group's official Investor Relations/newsroom as of 2026-07-21; Q2 results were released 2026-07-16): https://www.unitedhealthgroup.com/investors.html
- Stage 3b Bear advocate MISSING(malformed Sol output duplicated its case and echoed the bull; raw response preserved in 30-debate-bear-malformed.md and excluded, never repaired)
- P0.gap_pct deferred: needs today's open, not exposed by uw_bars (v1 uses move_pct/move_vs_atr)
- P0.catalyst_8k deferred: needs EDGAR submissions items endpoint (next slice)
- reddit.tradestie MISSING(URLError: <urlopen error timed out>)
- P6.youtube_attention (capped): count saturated at search page cap (50); true count >= cap
- P6.youtube_tone gated: classifier-validation (ERD R4/§10 build-dep, D1 open)
- P6.social_risk (capped): crowding hot but tone unavailable; label floor-bounded at 'none'

## P9 — left-side / mean-reversion signals

- [P9.base_rate_ci_note] = no confidence interval computed (n_macro=1 independent cycles is too few for one); treat the table as directional corroboration, not a calibrated probability label (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_direction] = up label (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_n_macro] = 1 count (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_n_raw] = 206 count (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_n_regimes] = 36 count (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_table] = [{'horizon_days': 5, 'n': 206, 'mean_pct': 0.6, 'median_pct': 0.6, 'winrate_pct': 54.4, 'avg_further_dd_pct': -2.9, 'worst_dd_pct': -24.1}, {'horizon_days': 10, 'n': 206, 'mean_pct': 0.7, 'median_pct': 0.9, 'winrate_pct': 57.3, 'avg_further_dd_pct': -4.8, 'worst_dd_pct': -31.7}, {'horizon_days': 20, 'n': 206, 'mean_pct': 2.8, 'median_pct': 2.3, 'winrate_pct': 58.7, 'avg_further_dd_pct': -6.7, 'worst_dd_pct': -34.7}, {'horizon_days': 40, 'n': 205, 'mean_pct': 5.6, 'median_pct': 5.0, 'winrate_pct': 66.8, 'avg_further_dd_pct': -9.4, 'worst_dd_pct': -52.6}, {'horizon_days': 60, 'n': 205, 'mean_pct': 6.6, 'median_pct': 6.5, 'winrate_pct': 70.7, 'avg_further_dd_pct': -11.5, 'worst_dd_pct': -52.6}] table (asof 2026-07-21, src derived(move_base_rate))
- [P9.base_rate_threshold_pct] = 3.51 pct (asof 2026-07-21, src derived(move_base_rate))
- [P9.climax] = False bool (asof 2026-07-21, src derived(stretch))
- [P9.climax_direction] = None label (asof 2026-07-21, src derived(stretch))
- [P9.cluster_events_n] = 207 count (asof 2026-07-21, src derived(move_cluster))
- [P9.cluster_k] = 21 count (asof 2026-07-21, src derived(move_cluster))
- [P9.cluster_status] = clustered label (asof 2026-07-21, src derived(move_cluster))
- [P9.exhaustion_crashfree_window] = True bool (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_direction] = overbought_turning_down label (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_higher_closes] = False bool (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_rsi_turn] = False bool (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_tally] = 1/4 ratio_label (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_tally_k] = 1 count (asof 2026-07-21, src derived(exhaustion))
- [P9.exhaustion_vol_decay] = False bool (asof 2026-07-21, src derived(exhaustion))
- [P9.move_atr] = 1.1962286159809863 ATRs (asof 2026-07-21, src derived(stretch))
- [P9.rsi_percentile_all] = 75.2 pctile (asof 2026-07-21, src derived(percentile))
- [P9.rsi_percentile_conditional] = 65.9 pctile (asof 2026-07-21, src derived(percentile))
- [P9.rsi_percentile_conditional_n] = 861 count (asof 2026-07-21, src derived(percentile))
- [P9.rsi_percentile_note] = differentiating label (asof 2026-07-21, src derived(percentile))
- [P9.stretch_sma200_atr] = 7.348971877541222 ATRs (asof 2026-07-21, src derived(stretch))
- [P9.stretch_sma20_atr] = 1.0655844268263202 ATRs (asof 2026-07-21, src derived(stretch))
- [P9.stretch_sma50_atr] = 2.424549120275739 ATRs (asof 2026-07-21, src derived(stretch))
- [P9.stretch_sma50_sigma] = 4.8134958224176785 sigma30_multiples (asof 2026-07-21, src derived(stretch))
- [P9.volume_climax_flag] = False bool (asof 2026-07-21, src derived(volume_climax))
- [P9.volume_decay_flag] = False bool (asof 2026-07-21, src derived(volume_climax))
- [P9.volume_zscore] = -0.01 sigma (asof 2026-07-21, src derived(volume_climax))
