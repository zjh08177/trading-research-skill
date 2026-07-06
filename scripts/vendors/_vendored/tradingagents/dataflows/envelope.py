"""Provenance-stamped data envelope (W1 T1.1).

Every datum that crosses the vendor/tool boundary is wrapped in an
:class:`Envelope` carrying a :class:`Provenance` stamp, so a downstream node
can cite *where* and *as of when* the data was true before stringifying it.

Two grounding rules are load-bearing:

* ``fetched_at`` is ALWAYS a tz-aware UTC instant (when we pulled the data).
* ``vendor_as_of`` is the instant the *vendor* asserts the data was true. When
  the vendor does not supply it, it stays ``None`` and is FLAGGED in the
  citation as unverified -- it is NEVER back-filled with ``now()`` or
  ``fetched_at``, because that would silently fabricate freshness.

Absence is an exception, not an empty Envelope: when a vendor has no usable
datum it raises :class:`~tradingagents.dataflows.errors.NoMarketDataError`
rather than constructing an ``Envelope`` with empty ``data``. This module only
references that error in the contract; it never raises it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Generic, TypeVar

from pydantic import BaseModel

from .errors import NoMarketDataError

T = TypeVar("T")


class Provenance(BaseModel):
    """Where a datum came from and as of when it was true."""

    vendor: str
    endpoint: str
    fetched_at: datetime
    vendor_as_of: datetime | None = None
    is_delayed: bool
    source: str | None = None

    def citation(self) -> str:
        """Render a non-empty, human-readable provenance line.

        Composed from ``source or vendor`` + ``endpoint`` + as-of + a
        delayed/realtime marker. ``vendor_as_of is None`` renders an explicit
        ``as-of unverified`` token (never ``fetched_at`` in disguise).
        """
        label = self.source or self.vendor
        if self.vendor_as_of is None:
            as_of = "as-of unverified"
        else:
            as_of = f"as of {self.vendor_as_of.date().isoformat()}"
        marker = "delayed" if self.is_delayed else "realtime"
        fetched = self.fetched_at.strftime("%Y-%m-%dT%H:%MZ")
        return f"{label} / {self.endpoint} — {as_of} (fetched {fetched}, {marker})"


class Envelope(BaseModel, Generic[T]):
    """A datum wrapped with its provenance stamp.

    ``data`` is the full object or the vendor raises ``NoMarketDataError`` --
    it is never partial or ``None``.
    """

    data: T
    provenance: Provenance

    def citation(self) -> str:
        """Delegate to the provenance stamp."""
        return self.provenance.citation()


def assert_envelope_contract(env: Envelope) -> None:
    """Assert the envelope-alone invariants; raise ``AssertionError`` on breach.

    The single source of the EC10 envelope-internal asserts -- reuse it, do not
    re-derive per call site. It checks ONLY what is decidable from the envelope
    itself; call-site add-ons needing external context (vendor matches the
    configured vendor, ``fetched_at`` within N minutes, ``vendor_as_of`` non-None
    for a given datum type, corpus has >=1 entry) live at the call site.
    """
    assert env.data is not None, (
        "envelope.data must not be None; an absent datum means the vendor should "
        f"raise {NoMarketDataError.__name__}, never construct an empty Envelope"
    )
    assert isinstance(env.provenance, Provenance), "provenance must be a Provenance"
    p = env.provenance
    assert isinstance(p.vendor, str) and p.vendor, "provenance.vendor must be a non-empty str"
    assert isinstance(p.endpoint, str) and p.endpoint, "provenance.endpoint must be a non-empty str"
    assert (
        p.fetched_at.tzinfo is not None and p.fetched_at.utcoffset() == timedelta(0)
    ), "provenance.fetched_at must be tz-aware UTC"
    assert (
        p.vendor_as_of is None or p.vendor_as_of.tzinfo is not None
    ), "provenance.vendor_as_of must be None or a tz-aware datetime"
    citation = p.citation()
    assert isinstance(citation, str) and citation, "provenance.citation() must be a non-empty str"
