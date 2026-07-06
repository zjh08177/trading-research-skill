# _vendored/ — self-contained import closure for the vendor CLIs

Vendored from the (now shelved) v1 fork `TradingAgents-upstream` @ commit
562866c on 2026-07-06. All code here was authored in-house for v1; the
`tradingagents` package name is kept so the 7 vendor CLIs' import lines
stay untouched. `_common.py` prepends this directory to sys.path.

Trimmed relative to the source: every `__init__.py` is EMPTY (the upstream
package inits pulled dotenv-usecwd, langchain warning shims, and the eval
judge/runner closure — none of which the CLIs need).

Modules: dataflows/{schwab, schwab_auth, schwab_options, edgar, marketaux,
symbol_utils, errors, bars, envelope, config} + default_config +
eval/acceptance/oracles/market_client. `schwab_auth` is lazy-imported inside
`schwab._get_access_token` — keep it when pruning.
