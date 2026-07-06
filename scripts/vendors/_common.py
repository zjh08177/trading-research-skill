"""Shared bootstrap for vendor CLIs. Must be imported before any tradingagents import."""
import json
import os
import sys

VENDORED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendored")
sys.path.insert(0, VENDORED)

from dotenv import load_dotenv

CREDS_PATH = os.environ.get(
    "TRADINGAGENTS_VENDORS_ENV",
    os.path.join(os.path.expanduser("~"), ".config", "tradingagents", "vendors.env"),
)
load_dotenv(CREDS_PATH, override=False)


def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def emit(facts):
    print(json.dumps(facts, separators=(",", ":")))


def die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)
