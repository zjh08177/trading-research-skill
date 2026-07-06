import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT":        "anthropic_effort",
    # Daily run-mode knobs (W2 T2.5). Coercion follows each default's type: the
    # two counts stay int, the budget stays float.
    "TRADINGAGENTS_DAILY_TOP_K":          "daily_top_k",
    "TRADINGAGENTS_DAILY_BUDGET_USD":     "daily_budget_usd",
    "TRADINGAGENTS_DAILY_MAX_UNSCOREABLE": "daily_max_unscoreable",
    # Dotted key targets a nested data_vendors entry, so a category vendor can be
    # flipped per-run with zero code change (the live provider switch; default
    # stays yfinance so offline tests are untouched unless this var is set).
    "TRADINGAGENTS_CORE_STOCK_APIS":      "data_vendors.core_stock_apis",
    "TRADINGAGENTS_NEWS_DATA":            "data_vendors.news_data",
}


_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place.

    A dotted key (e.g. ``data_vendors.core_stock_apis``) targets a nested dict
    entry; a plain key targets a top-level one. Coercion follows the existing
    default's type either way.
    """
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        parent_path, _, leaf = key.rpartition(".")
        dest = config
        for part in parent_path.split(".") if parent_path else ():
            dest = dest[part]
        try:
            dest[leaf] = _coerce(raw, dest.get(leaf))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Permanent, human-readable per-run report archive (§5b). Each `analyze` writes
    # one markdown file here so re-reading a past analysis never re-spends ~$0.85.
    # Point it at an Obsidian vault folder to browse reports there; the tool stays
    # vault-agnostic (it just writes markdown to this dir).
    "report_dir": os.getenv("TRADINGAGENTS_REPORT_DIR", os.path.join(_TRADINGAGENTS_HOME, "reports")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    "data_vendors": {
        # v1.0 ships on the reliable paid/authoritative APIs we hold keys for
        # (schwab prices, marketaux news, edgar SEC fundamentals) — never the
        # unofficial yfinance default (it returned no rows for MRVL and its
        # adjusted-close skewed the acceptance indicator recompute). 2026-07-01.
        "core_stock_apis": "schwab",         # prices/OHLCV — Schwab (real-time, paid). Options: alpha_vantage, yfinance, schwab
        "technical_indicators": "yfinance",  # LLM's supplementary get_indicators only; the GROUNDED indicators are computed locally from the Schwab snapshot. schwab lacks get_indicators; no alpha_vantage key. Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Analysis-layer (NOT grounded/N1); compact. edgar's raw XBRL overflows the LLM context → deferred to v1.1 with a summariser (+ EDGAR grounding/GB1). Options: edgar, alpha_vantage, yfinance
        "news_data": "marketaux,yfinance",   # marketaux (keyed) for ticker news; yfinance covers get_global_news + get_insider_transactions (marketaux lacks them). Options: marketaux, alpha_vantage, yfinance
        "macro_data": "fred",                # Options: fred (needs FRED_API_KEY; optional — degrades if unset)
        "prediction_markets": "polymarket",  # Options: polymarket (keyless)
        "options_chain": "schwab",           # Options: schwab (needs SCHWAB_ACCESS_TOKEN), fixture
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Daily / ad-hoc run modes (W2 T2.5).
    # daily_top_k          — watchlist names deep-analyzed per day (held are extra).
    # daily_budget_usd     — hard $ cap; the daily loop stops once metered spend
    #                        reaches it (held-past-cap warns loudly, never silent).
    # daily_max_unscoreable (M) — > M unscoreable watchlist names skips the batch.
    # prerank_weights      — weights for the cheap pre-rank score.
    # prerank_rel_volume_window — lookback (bars) for the relative-volume signal.
    "daily_top_k": 5,
    "daily_budget_usd": 50.0,
    "daily_max_unscoreable": 3,
    "prerank_weights": {
        "abs_move_1d": 1.0,
        "gap_pct": 1.0,
        "rel_volume": 0.5,
        "news_count_24h": 0.2,
    },
    "prerank_rel_volume_window": 20,
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
})
