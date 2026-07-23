"""P6 Reddit crowding distiller (ERD R2/§4): apewisdom leaderboard row ->
`P6.reddit_crowding`.

Crowding = attention/mention volume, an explicit RISK input (never a quality
signal). `notable` keys off the 24h delta (ERD §4: "notable: bool (off
24h-delta)"). Absence from the scanned ranks is a valid R5 quiet reading.

Null-tolerant by contract (B1): malformed/missing vendor fields degrade to a
named gap, never a fabricated number.
"""

MENTION_SPIKE_X = 1.5      # mentions >= 1.5x their 24h-ago level
RANK_JUMP_NOTABLE = 20     # or the name climbed >= 20 leaderboard ranks in 24h


def distill(raw, ctx) -> list:
    raw = raw or {}
    asof = raw.get("asof") or ctx.asof
    row = raw.get("row")

    if row is None:
        scanned = raw.get("scanned_ranks")
        return [{
            "id": "P6.reddit_crowding",
            "v": f"quiet: not in top ~{scanned or 300} Reddit crowding ranks",
            "unit": "none", "asof": asof, "src": "apewisdom",
            "low_trust": True, "notable": False,
        }]

    mentions = row.get("mentions")
    if not isinstance(mentions, (int, float)):
        return [{
            "id": "P6.reddit_crowding", "v": None, "unit": "mentions",
            "asof": asof, "src": "apewisdom",
            "gap": "vendor row carried no numeric mentions",
        }]

    mentions = int(mentions)
    prior = row.get("mentions_24h_ago")
    rank = row.get("rank")
    rank_prior = row.get("rank_24h_ago")

    spike = bool(isinstance(prior, (int, float)) and prior > 0
                 and mentions >= MENTION_SPIKE_X * prior)
    rank_jump = bool(isinstance(rank, (int, float)) and isinstance(rank_prior, (int, float))
                     and (rank_prior - rank) >= RANK_JUMP_NOTABLE)

    sig = {
        "id": "P6.reddit_crowding", "v": mentions, "unit": "mentions",
        "asof": asof, "src": "apewisdom",
        "notable": spike or rank_jump, "low_trust": True,
    }
    if isinstance(rank, (int, float)):
        sig["rank"] = int(rank)
    if isinstance(prior, (int, float)):
        sig["mentions_24h_ago"] = int(prior)   # R16/D6 extra field: the delta basis
    if isinstance(rank_prior, (int, float)):
        sig["rank_24h_ago"] = int(rank_prior)
    return [sig]
