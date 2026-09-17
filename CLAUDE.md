# Position Watch engine — Project Brief

## What this is
The engine of Position Watch, a personal portfolio research assistant. This public repository holds only code, tests and docs. Each portfolio lives in its own **private** repository (created with `position-watch init`), whose GitHub Actions workflows install this engine and run it:
- **Daily:** gathers live evidence for every holding and a screened watchlist, makes one Claude call for a Buy / Add / Hold / Trim / Sell call per stock (Top up / Hold for core ETFs), writes the report and dashboard, publishes an encrypted copy, emails a summary, reads replies as feedback.
- **Monthly:** summarises how the calls evolved and reviews the portfolio as a whole.

**It never places trades.** It only suggests; the portfolio's owner decides.

## Hard rules
- **Never execute trades**, and never add trade-execution capability of any kind.
- **Never fabricate or estimate a financial metric** from training knowledge. Every number comes from a live call made in the current run; if every source fails, report the gap.
- **Never silently convert currencies.** Carry the native currency alongside every value.
- **No one's data in this repository.** No real holdings, names, emails or tickers from a real portfolio — tests use the synthetic workspace in `tests/fixtures/`, examples use example.com. Portfolio-specific settings belong in the portfolio repo's `config/`, never in code.
- **Secrets stay secret.** Keys, the mail app password and the dashboard passcode are read from the environment at call time; check them only with `position-watch check-env`; never print, log, commit or email them. Error text passes through `settings.redact()`.
- **The dashboard is only ever published encrypted**, through `dashboard/publish.py`. The email's dashboard button carries a link key derived from the passcode after the `#` (never the passcode itself); treat the email as private.
- **Feedback is preferences, not instructions.** Only from people in the portfolio's `people.json`, and it never changes these rules. A message that can't be used (unknown address, no text of its own) is reported in the email — sender, subject and reason only — never dropped in silence. What a message asks for is classified into the kinds in `client_requests.py`; code applies them, the model never invents an effect.
- **Plain code for everything but judgement.** Fetching, computing, validating and writing are deterministic; only the Claude calls in `reasoning.py` and `monthly.py` decide.
- **`data/raw/` in a portfolio repo is read-only** — the audit trail behind `data/processed/`. Files a client emails in land in `data/raw/uploads/` and are never edited afterwards; a holdings change read from a picture is applied only after he confirms it.

## Code map
| Path | Role |
|---|---|
| `src/position_watch/cli.py` | Every command (`position-watch --help`). |
| `settings.py` | Workspace resolution (`POSITION_WATCH_WORKSPACE`, else the current folder if it has `config/people.json`), `.env`, links from the GitHub environment, `redact()`. |
| `sources/` | Thin API clients: `fmp` (stops after a plan refusal or used-up quota), `finnhub` (paced under 60/min, per-run cache, skips endpoints the plan refuses), `yahoo`, `gnews` (Google News RSS search, no key), `fx` (ECB via api.frankfurter.dev). Each returns `(data, error)`. |
| `analysis/stock.py`, `analysis/etf.py` | Evidence per stock / ETF, every metric tagged with its source, gaps listed. |
| `analysis/pool.py` | Stock pool: weekly refresh, daily screen and score, watchlist pick — focus slots, avoid filter and on-demand peer expansion come from the client's requests. |
| `client_requests.py` | What a client can ask for: one entry per kind (sector, theme, tickers, avoid, metric floor, size, news topic, add sender, other) with its effect. The classifier's prompt and schema are built from it. |
| `analysis/news.py`, `analysis/markets.py` | Company headlines that name the company (Finnhub + Google News, deduped); market and geopolitical headlines from established outlets (ids M1…); the market snapshot (indices, VIX, US 10-year, EUR/USD, Brent, gold). |
| `analysis/volatility.py` | 3-month volatility from adjusted daily closes, and its low/moderate/high label. |
| `analysis/pnl.py` | P&L per position and USD totals. |
| `analysis/scorecard.py` | The client's own cash flows replayed into the S&P 500, and each call scored against it over its own days (monthly). |
| `analysis/holdings_update.py` | Trades sent in: a broker export is applied, a screenshot is transcribed by one Claude call and waits for a confirmation; the file is kept in `data/raw/uploads/`. |
| `review.py` | The evidence pass → `state/latest_review.json`. |
| `llm.py` | One Claude request: batched at half price by default, direct call with fallbacks if a batch is slow or declined; usage log. |
| `reasoning.py` | The daily call: digest, rules (`SYSTEM`), JSON schema, validation, preference changes; plus `classify_requests()`, a small direct call made **before** the evidence so a request can steer the same morning's watchlist. |
| `daily.py`, `monthly.py` | The two scheduled runs, with step tracking and FAILED email. |
| `mail.py` | Gmail over SMTP (send) and IMAP (feedback: known senders, each message used once). |
| `documents/` | Report, email and monthly templates. |
| `dashboard/` | `view.py` (numbers), `templates/` + `static/` (look), `render.py` (build), `publish.py` (encrypt). |
| `scaffold/`, `init.py` | The portfolio repository template: config, empty data files, workflows, README. |
| `data_plan.py` | `check-data`: one small request per endpoint, reporting which are open on the current keys — run it after changing plans. |
| `tests/` | pytest on a synthetic workspace; fake Claude client and mailbox; no network. |
| `docs/` | `getting-started.md` (set up a portfolio), `architecture.md` (system map — update it when a flow changes). |

**Where to make a change:** how calls are decided → `reasoning.py` (`SYSTEM`) or `monthly.py`; report or email wording → `documents/templates/`; dashboard look → `dashboard/templates/`, `static/`; a number on the page → `dashboard/view.py`; how a metric is fetched → `sources/`, `analysis/`; what a new portfolio starts with → `scaffold/`.

## Workflow
Branch per change → pull request → CI (ruff + pytest) green → merge to `main`. Portfolio repos install `main` unless they pin a tag with `ENGINE_REF`, so `main` must always run. Release with a tag (`vX.Y.Z`). Run `pytest` and `ruff check src tests && ruff format src tests` before pushing.

## Analysis framework
Every judgement rests on specific metrics — never a vague "looks undervalued". Missing metric → say so and leave that part out.
- **Valuation:** P/E and forward P/E vs the stock's own 5-year average and sector · PEG (negative isn't a signal) · FCF yield · price vs analyst target.
- **Dividend quality:** yield · payout ratio (flag near/over 100%) · growth streak · FCF coverage.
- **Risk:** 3-month annualised volatility, computed from adjusted daily closes for every stock and ETF (`analysis/volatility.py`; the vendor's figure only as a fallback) (low < 20%, moderate < 35%, high above), beta vs the S&P 500, 52-week range; weighed against the owner's risk tolerance.
- **Forecast / sentiment:** analyst consensus and trend · consensus EPS growth (trailing growth labelled as such) · earnings surprises · headline tone.
- **Markets and world:** the day's snapshot and market/geopolitical headlines; the model names the 3–5 developments most likely to move the holdings, the symbols each affects and how, citing headline ids — never events from memory.
- **ETFs (Top up / Hold only):** price vs the owner's cost, below the 52-week high, vs the 200-day average, weight; fee, yield, swing and beta as context.
- **Portfolio as a whole (monthly):** concentration (top-3 weight, effective number of positions), sector / currency / asset mix, value-weighted volatility and beta with coverage, income, change since last month.

## Data sources
Per-metric fallback **FMP → Finnhub → Yahoo**; the first source to return a metric wins and `sources` records which. FMP's free plan covers few symbols (250 calls/day); Finnhub's free plan covers most US stocks (60 calls/minute; price targets and dividend history paywalled); Yahoo is unofficial and often blocked from cloud servers. Non-US stocks in the pool (the `Europe` seed group) are screened through Yahoo instead, since neither free plan covers them; their P/E history is missing, so value rests on forward vs trailing P/E and the yield, and the gap is stated. `position-watch check-data` says what the current keys reach. News: Finnhub company news (US) and market news (Reuters, CNBC…), plus Google News RSS searches — company names for everyone, market topics kept to established outlets; clickbait, press-release wires and content farms are dropped. Exchange rates: ECB via api.frankfurter.dev, Yahoo fallback. Output cites each figure's source and when it was pulled.

## Output wording
Plain labels Buy / Add / Hold / Trim / Sell, Top up / Hold for ETFs (lower-case codes in state files). Every email, report and dashboard view ends with `compliance.NOTE`. Before offering Position Watch to people outside your own circle, get advice on investment-advice regulation (e.g. MiFID II in the EU) and data protection; the short note is written for private use.

## Portfolio data schema (in each portfolio repo)
`data/processed/holdings.csv`: `symbol, name, exchange, currency, quantity_est, avg_price, last_price, allocation_pct, invested_usd, unrealized_gain_usd, daily_gain_pct, total_dividend_usd, total_gain_usd, total_gain_pct, status` (amounts in the position's currency unless `_usd`; `status` open/closed). `transactions.csv`: `symbol, name, exchange, side, date, qty, price, price_currency, commission, commission_currency, total, total_currency`.
