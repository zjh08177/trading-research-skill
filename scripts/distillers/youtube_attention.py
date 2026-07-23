"""P6 YouTube attention distiller (ERD R3/R4/§4): youtube_data search counts ->
`P6.youtube_attention`, plus the R4 tone gate.

`P6.youtube_tone` is emitted ONLY as a named gap ("gated: classifier-
validation") until the ERD §10 classifier + labeled-sample accuracy gate is
built (open decision D1) — never as a fabricated score. Cite-by-ID only
(ERD R14): video IDs ride as an R16 extra field; no titles/UGC text persist.
"""

ATTENTION_NOTABLE_MIN = 30  # near-saturating a 7d/50-cap search page = hot


def distill(raw, ctx) -> list:
    raw = raw or {}
    asof = raw.get("asof") or ctx.asof
    count = raw.get("video_count")

    if not isinstance(count, (int, float)):
        return [{
            "id": "P6.youtube_attention", "v": None, "unit": "videos",
            "asof": asof, "src": "youtube(search.list)",
            "gap": "vendor payload carried no numeric video_count",
        }]

    cap = raw.get("capped_at")
    window = raw.get("window_days")
    sig = {
        "id": "P6.youtube_attention", "v": int(count), "unit": "videos",
        "asof": asof, "src": "youtube(search.list)",
        "notable": int(count) >= ATTENTION_NOTABLE_MIN, "low_trust": True,
    }
    if isinstance(window, (int, float)):
        sig["window_days"] = int(window)
    if isinstance(cap, (int, float)) and count >= cap:
        sig["gap"] = f"count saturated at search page cap ({int(cap)}); true count >= cap"
    ids = raw.get("video_ids")
    if isinstance(ids, list) and ids:
        sig["video_ids"] = [str(i) for i in ids][:5]   # R14 cite-by-ID

    gate = {
        "id": "P6.youtube_tone", "v": None, "unit": "score[-1,1]",
        "asof": asof, "src": "youtube+classifier",
        "gap": "gated: classifier-validation (ERD R4/§10 build-dep, D1 open)",
    }
    return [sig, gate]
