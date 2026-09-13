# My portfolio — Position Watch

This private repository holds one portfolio's data for [Position Watch](https://github.com/alexdiehl0/position-watch-engine): your holdings, preferences, daily reports and dashboard. The code lives in the engine repository; the workflows here install it and run it every day.

**It never places trades.** It only suggests; you decide. Not financial advice.

## What's here

| Folder | What it holds | Who writes it |
|---|---|---|
| `config/people.json` | Who gets the daily email, and your preferences (horizon, goal, risk tolerance, what to favour or avoid) | You; the daily run records preferences you state in feedback |
| `config/instruments.json` | Which holdings are ETFs or commodities, and Yahoo tickers that differ from your broker's | You |
| `config/stock_universe.json` | The starting list the watchlist is screened from | You (optional) |
| `data/processed/holdings.csv` | Your positions (columns below) | You |
| `data/processed/transactions.csv` | Your trades (optional) | You |
| `data/raw/` | Broker exports and screenshots, kept as they came | You |
| `state/`, `reports/`, `site/` | Evidence, calls, history, daily and monthly reports, the dashboard | The runs |

## Holdings file

One row per position in `data/processed/holdings.csv`. Amounts are in the position's own currency unless the column says `usd`.

| Column | Meaning |
|---|---|
| `symbol`, `name`, `exchange` | As your broker shows them |
| `currency` | The currency the position is priced in (e.g. USD, EUR) |
| `quantity_est` | Number of shares |
| `avg_price`, `last_price` | Your average cost and the broker's last price |
| `allocation_pct` | Share of the portfolio, % |
| `invested_usd`, `unrealized_gain_usd`, `total_dividend_usd`, `total_gain_usd` | From your broker, in USD |
| `daily_gain_pct`, `total_gain_pct` | From your broker, % |
| `status` | `open` or `closed` |

Update it whenever you buy or sell, then commit.

## Secrets (Settings → Secrets and variables → Actions)

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude API key (console.anthropic.com), billed to you |
| `FMP_API_KEY`, `FINNHUB_API_KEY` | Free market-data keys (financialmodelingprep.com, finnhub.io) |
| `MAIL_USER`, `MAIL_APP_PASSWORD` | The Gmail that sends your daily email and receives your replies, and a Gmail app password for it |
| `DASHBOARD_PASSCODE` | Optional: the passcode that locks your published dashboard |
| `PAGES_TOKEN` | Optional: a token that can write to your dashboard repo |

Optional repository variables: `PAGES_REPOSITORY` (owner/name of your public dashboard repo), `ENGINE_REF` (the engine version to run, e.g. `v0.4.0`; default `main`).

## Running it yourself

```bash
pip install "position-watch @ git+https://github.com/alexdiehl0/position-watch-engine"
cp .env.example .env            # fill in your keys
position-watch check-env
position-watch daily --no-email # one full run without email
position-watch dashboard build  # writes site/index.html
```
