#!/usr/bin/env python3
"""Deterministic Mean-Reversion / Exhaustion block: read the P9 facts from
10-datapack.json and emit a verbatim `53-meanrev-block.md` (mirrors
render_options.py -> 52-options-block.md). The writer inserts this block
VERBATIM under the `## Mean-Reversion / Exhaustion` slot and never
regenerates it (Invariant 2). Stdlib only.

Usage: render_meanrev.py <datapack.json>
Exit 0 ok; 3 when the pack carries no P9 facts (should not happen — this
analyst is always-on); 2 on bad args."""
import json
import sys

SPEC = [
    ("P9.stretch_sma20_atr", "Stretch vs SMA20", "atr"),
    ("P9.stretch_sma50_atr", "Stretch vs SMA50", "atr"),
    ("P9.stretch_sma200_atr", "Stretch vs SMA200", "atr"),
    ("P9.stretch_sma50_sigma", "Stretch vs SMA50 (sigma30 units)", "raw"),
    ("P9.move_atr", "Today's move (ATR14 units)", "atr"),
    ("P9.climax", "Climax day (>=1.5x ATR)", "label"),
    ("P9.climax_direction", "Climax direction", "label"),
    ("P9.decay_risk_daily_pct", "Leveraged decay drag", "pct_native"),
    ("P9.rsi_percentile_all", "RSI14 percentile (all-history)", "pct_native"),
    ("P9.rsi_percentile_conditional", "RSI14 percentile (comparable-move-conditioned)", "pct_native"),
    ("P9.rsi_percentile_conditional_n", "Conditional RSI sample size", "raw"),
    ("P9.rsi_percentile_note", "RSI edge note", "label"),
    ("P9.volume_zscore", "Volume z-score", "raw"),
    ("P9.volume_climax_flag", "Volume climax", "label"),
    ("P9.volume_decay_flag", "Volume climax-then-decay", "label"),
    ("P9.cluster_status", "Regime status", "label"),
    ("P9.cluster_k", "Cluster size (comparable moves, trailing ~60 sessions)", "raw"),
    ("P9.cluster_events_n", "Total comparable-move events (incl. today)", "raw"),
    ("P9.base_rate_n_raw", "Base-rate sample (raw occurrences)", "raw"),
    ("P9.base_rate_n_regimes", "Base-rate sample (regime-clustered)", "raw"),
    ("P9.base_rate_n_macro", "Base-rate sample (macro-cycle)", "raw"),
    ("P9.base_rate_direction", "Base-rate direction studied", "label"),
    ("P9.base_rate_threshold_pct", "Base-rate move threshold", "pct_native"),
    ("P9.base_rate_ci_note", "Base-rate confidence-interval caveat", "label"),
]
LISTS = [
    ("P9.base_rate_table", "Forward-return base rate by horizon",
     ["Horizon (d)", "n", "Mean %", "Median %", "Win %", "Avg further DD %", "Worst DD %"]),
]


def _fmt(fact, mode):
    v = fact.get("v")
    if v is None:
        return "DATA GAP"
    if mode == "label":
        return str(v)
    if mode == "atr":
        return f"{v:+.2f}x"
    if mode == "pct_native":
        return f"{v:g}%"
    return f"{v:g}"


def build(pack):
    present = [(fid, label, mode) for fid, label, mode in SPEC if fid in pack]
    present_lists = [(fid, header, cols) for fid, header, cols in LISTS if fid in pack]
    if not present and not present_lists:
        raise KeyError("no P9.* facts present in pack")

    lines = ["<!-- meanrev-block: inserted verbatim, do not edit -->",
             "### Mean-Reversion / Exhaustion (computed)"]
    for fid, label, mode in present:
        fact = pack[fid]
        lines.append(f"- {label}: {_fmt(fact, mode)} [{fid}]")
    for fid, header, cols in present_lists:
        fact = pack[fid]
        if not fact.get("v"):
            continue
        rows = fact["v"]
        lines.append(f"\n**{header}** [{fid}]\n")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for row in rows:
            lines.append("| " + " | ".join(str(row.get(k, "")) for k in
                        ("horizon_days", "n", "mean_pct", "median_pct",
                         "winrate_pct", "avg_further_dd_pct", "worst_dd_pct")) + " |")
    lines.append("<!-- meanrev-block: end -->")
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: render_meanrev.py <datapack.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    try:
        block = build(pack)
    except KeyError as e:
        sys.stderr.write(f"ERROR: no P9 facts to render: {e.args[0]}\n")
        return 3
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
