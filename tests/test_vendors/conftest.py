import sys
from pathlib import Path

VENDORS = str(Path(__file__).resolve().parents[2] / "scripts" / "vendors")
if VENDORS not in sys.path:
    sys.path.insert(0, VENDORS)
