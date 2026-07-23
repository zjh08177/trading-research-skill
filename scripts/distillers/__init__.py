"""distillers package: raw-vendor-row -> Signal derivation contract (R1-R5).

Re-exports the shared contract from _base so callers do `from distillers
import signal, DistillCtx, merge_signals` without knowing the internal
module split.
"""
from ._base import signal, DistillCtx, merge_signals

__all__ = ["signal", "DistillCtx", "merge_signals"]
