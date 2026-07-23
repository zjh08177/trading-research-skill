"""P6 Reddit/WSB tone distiller (ERD R1/§4): tradestie leaderboard row ->
`P6.reddit_tone`.

FRAMING (ERD R6, ratified v2.7): the signed reading is CROWDING-DIRECTION and
carries `low_trust: true` — it is never a thesis/quality input; tone reaches
thesis synthesis only via `P6.social_risk`. Absence from the top-50 is a valid
R5 quiet reading (no measurable WSB crowd tone), not a gap.

Null-tolerant by contract (B1): malformed/missing vendor fields degrade to a
named gap, never a fabricated number.
"""

BULLISH_LABEL_MIN = 0.05
BEARISH_LABEL_MAX = -0.05


def _label(score):
    if score >= BULLISH_LABEL_MIN:
        return "bull"
    if score <= BEARISH_LABEL_MAX:
        return "bear"
    return "neutral"


def distill(raw, ctx) -> list:
    raw = raw or {}
    asof = raw.get("date") or ctx.asof
    row = raw.get("row")

    if ctx.mode == "replay" and str(asof)[:10] >= str(ctx.asof)[:10]:
        # R9 belt-and-braces: the vendor CLI already asserts date < cutoff.
        return [{
            "id": "P6.reddit_tone", "v": None, "unit": "score[-1,1]",
            "asof": asof, "src": "tradestie",
            "gap": f"replay guard: leaderboard date {asof} not strictly before cutoff {ctx.asof}",
        }]

    if row is None:
        n = raw.get("n_ranked")
        return [{
            "id": "P6.reddit_tone",
            "v": f"quiet: not on WSB top-{n or 50} leaderboard (no measurable crowd tone)",
            "unit": "none", "asof": asof, "src": "tradestie",
            "label": "not-ranked", "low_trust": True, "notable": False,
        }]

    score = row.get("sentiment_score")
    if not isinstance(score, (int, float)):
        return [{
            "id": "P6.reddit_tone", "v": None, "unit": "score[-1,1]",
            "asof": asof, "src": "tradestie",
            "gap": "vendor row carried no numeric sentiment_score",
        }]

    sig = {
        "id": "P6.reddit_tone", "v": round(float(score), 4),
        "unit": "score[-1,1]", "asof": asof, "src": "tradestie",
        "label": _label(float(score)), "low_trust": True,
    }
    comments = row.get("no_of_comments")
    if isinstance(comments, (int, float)):
        sig["n_comments"] = int(comments)
    return [sig]
