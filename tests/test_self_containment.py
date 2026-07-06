"""Self-containment contract: the coupled vendor CLIs must run without any
workspace checkout — imports resolve from scripts/vendors/_vendored/ only.

Probes each CLI in a subprocess with a sanitized env (no PYTHONPATH /
VIRTUAL_ENV, cwd=/) and asserts no imported module originates from the
shelved tradingagents-workspace tree.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

VENDORS = Path(__file__).resolve().parents[1] / "scripts" / "vendors"

COUPLED = [
    "schwab_quote",
    "schwab_bars",
    "schwab_options",
    "schwab_account",
    "edgar_fundamentals",
    "marketaux_news",
    "tiingo_oracle",
]


def _probe(mod):
    snippet = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(VENDORS)!r})\n"
        f"import {mod}\n"
        "import tradingagents.dataflows.schwab_auth as sa\n"
        "leaks = sorted({m.__file__ for m in list(sys.modules.values())"
        " if getattr(m, '__file__', None) and 'tradingagents-workspace' in m.__file__})\n"
        "print(json.dumps({'leaks': leaks, 'schwab_auth': sa.__file__}))\n"
    )
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, cwd="/", env=env,
    )


@pytest.mark.parametrize("mod", COUPLED)
def test_cli_self_contained(mod):
    r = _probe(mod)
    assert r.returncode == 0, f"{mod} failed in sanitized env:\n{r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["leaks"] == [], f"{mod} imported from the workspace: {out['leaks']}"
    assert str(VENDORS / "_vendored") in out["schwab_auth"], out["schwab_auth"]
