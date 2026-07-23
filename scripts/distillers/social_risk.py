"""P6 social risk distiller (ERD R6/§4): the ONLY door tone gets into
thesis-forming synthesis — as a crowding-conjoined risk-asymmetry label.

Pure derive-only (raw ignored): reads the P6 atomics already merged into
`ctx.facts` by the earlier registry entries, so this feed MUST be registered
after reddit_tone / reddit_crowding / youtube_attention.

Labels:
  squeeze-risk  — crowding hot + crowd tone bearish (negative pile-on: squeeze
                  fuel if price disagrees with the crowd)
  reversal-risk — crowding hot + crowd tone euphoric-bullish (one-sided
                  crowded long: air-pocket asymmetry)
  none          — no hot crowding, or tone too weak to conjoin

`agreement` counts platform-INDEPENDENT substrates only (ERD R6): Tradestie +
ApeWisdom are both Reddit-derived => one substrate; YouTube adds the second.
"""

TONE_STRONG = 0.10  # |tone| below this never conjoins into a risk label


def _num(fact_val):
    return fact_val if isinstance(fact_val, (int, float)) else None


def distill(raw, ctx) -> list:
    facts = ctx.facts or {}
    asof = ctx.asof

    crowding = facts.get("P6.reddit_crowding") or {}
    attention = facts.get("P6.youtube_attention") or {}
    tone_fact = facts.get("P6.reddit_tone") or {}

    reddit_hot = crowding.get("notable") is True
    youtube_hot = attention.get("notable") is True
    crowding_hot = reddit_hot or youtube_hot

    substrates = set()
    if crowding or tone_fact:
        substrates.add("reddit")       # tradestie+apewisdom = ONE substrate (R6)
    if attention:
        substrates.add("youtube")
    agreement = len(substrates)

    if not substrates:
        return [{
            "id": "P6.social_risk", "v": None, "unit": "label",
            "asof": asof, "src": "derived(P6)",
            "gap": "no social atomics present to derive risk from",
        }]

    tone = _num(tone_fact.get("v"))

    if crowding_hot and tone is not None and abs(tone) >= TONE_STRONG:
        label = "squeeze-risk" if tone < 0 else "reversal-risk"
        return [{
            "id": "P6.social_risk", "v": label, "unit": "label",
            "asof": asof, "src": "derived(P6)",
            "agreement": agreement, "notable": True,
        }]

    sig = {
        "id": "P6.social_risk", "v": "none", "unit": "label",
        "asof": asof, "src": "derived(P6)",
        "agreement": agreement, "notable": False,
    }
    if crowding_hot and tone is None:
        # Crowding fired but the tone leg is missing (e.g. tradestie outage):
        # say so — a silent "none" would understate live risk (no-silent-failure).
        sig["gap"] = "crowding hot but tone unavailable; label floor-bounded at 'none'"
    return [sig]
