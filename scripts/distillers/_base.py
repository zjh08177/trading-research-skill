"""Shared contract for feed distillers (R1-R5, tech-solution §3).

`signal()` builds a Signal dict (a fact plus optional salience metadata).
`DistillCtx` is the frozen, read-only context every distiller receives.
`merge_signals()` is the ONE place R3 (salience cap + named omission), R4
(mandatory citation) and R5 (explicit quiet) are enforced, so individual
distillers stay pure derive-only functions.
"""
from dataclasses import dataclass
from typing import Any, Optional


def signal(id, v, unit, asof, src, rank=None, notable=None, gap=None) -> dict:
    """Build a Signal dict. Optional keys (rank/notable/gap) are omitted when
    left as None so they never appear as spurious null keys in the pack."""
    s = {"id": id, "v": v, "unit": unit, "asof": asof, "src": src}
    if rank is not None:
        s["rank"] = rank
    if notable is not None:
        s["notable"] = notable
    if gap is not None:
        s["gap"] = gap
    return s


@dataclass(frozen=True)
class DistillCtx:
    """Read-only context passed to every distiller's distill(raw_rows, ctx)."""
    ticker: str
    kind: str
    asof: str
    mode: str
    facts: dict
    spot: Optional[float]
    atr: Optional[float]
    max_rows: int
    max_tokens: Optional[int]
    entry: Any


def merge_signals(facts, gaps, signals, cite_src):
    """Merge a distiller's returned Signals into `facts`/`gaps` in place.

    R4 (citation): a Signal with no `src` is back-filled from `cite_src` (the
    feed's resolvable vendor:endpoint) before anything else happens.
    R3 (salience cap): a Signal with v=None and a `gap` is a pure omission
    line — it names dropped detail and is NEVER stored as a fact. A Signal
    that carries both a value AND a `gap` is a capped-but-kept fact: it is
    stored under its id, and its omission is ALSO named in gaps (prefixed
    "(capped): ") so truncation is never silent.
    R5 (quiet): a Signal with notable=False is just a normal fact — the
    "quiet: <reason>" value IS the legible line, so no gap accounting is
    needed for it.
    """
    for s in signals:
        if s.get("src") is None:
            s = {**s, "src": cite_src}
        fid = s["id"]
        if s.get("v") is None and s.get("gap"):
            gaps.append(f"{fid} {s['gap']}")
            continue
        facts[fid] = {k: v for k, v in s.items() if k != "id"}
        if s.get("gap"):
            gaps.append(f"{fid} (capped): {s['gap']}")
