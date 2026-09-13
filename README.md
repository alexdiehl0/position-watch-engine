# Position Watch

A personal portfolio research assistant that runs itself. Every morning it gathers live market data for your holdings and a screened watchlist, has Claude turn that evidence into Buy / Add / Hold / Trim / Sell calls (Top up / Hold for core ETFs) with the reasoning behind each, and sends you an email with a passcode-locked dashboard. Once a month it reviews how your portfolio works as a whole. Reply to any email and the next run takes it into account.

**It never places trades.** It only suggests; you decide. Not financial advice.

This repository is the **engine**: the code, tests and documentation, with no one's data in it. Each portfolio lives in its own private repository, created from the built-in template, whose workflows install this engine and run it.

→ **New here? Follow [docs/getting-started.md](docs/getting-started.md)** — about 20 minutes.

## How it works

```
your private portfolio repo                     this engine (public)
  config/ · data/ · state/ · reports/             installed by the workflows
  .github/workflows/daily.yml ──── installs ────► position-watch daily
            │
            ▼
  evidence ──► FMP → Finnhub → Yahoo per metric, ECB exchange rates (plain code, every figure tagged with its source)
            ──► ONE Claude Opus 5 call, batched at half price, structured output checked by code
            ──► report · dashboard · email ──► your inbox, and a locked page on GitHub Pages
```

Plain code fetches and computes every number and runs every step; the single AI call only reasons over that evidence, so it can't invent a figure or skip a step. A day costs one Claude request — typically well under a dollar — logged in `state/api_usage.csv`. The full system map is in [docs/architecture.md](docs/architecture.md).

## Commands

Installed as `position-watch` (or `python -m position_watch`), run from a portfolio repository:

| Command | What it does |
|---|---|
| `init DIR` | Create a new portfolio repository from the template |
| `daily` | The whole morning run: evidence, one Claude call, report, dashboard, email (`--pages-dir`, `--no-email`) |
| `monthly` | The month's calls and a review of the portfolio as a whole |
| `check-env` | Say which secrets are set — never their values |
| `review` | Gather the evidence only (4–5 minutes; Finnhub calls are paced to the free limit) |
| `dashboard build` / `dashboard publish DIR` | Build the dashboard; write the encrypted copy for GitHub Pages |
| `pnl`, `people`, `recipients`, `who-sent`, `prefs-update`, `handoff`, `log-suggestions`, `compliance` | Smaller building blocks |

## Costs

- **Market data:** free tiers of FMP and Finnhub, Yahoo Finance and the ECB — no cost.
- **Claude:** one Claude Opus 5 request a day and one a month, on your own Anthropic API key, sent through the Message Batches API at half price (it falls back to a direct request if a batch is slow). Input is a compact digest of the evidence.
- **GitHub:** a private repository and Actions minutes within the free allowance.

## Security

- Keys, the mail app password and the dashboard passcode live only in your `.env` and your repository's secrets. The code reads them at call time, never prints them, and removes their values from any error text before it reaches a log, report or email.
- The public dashboard is encrypted before upload (AES-256-GCM, key derived from your passcode with PBKDF2-SHA256, 600,000 rounds). Without the passcode it shows nothing, and there is no unencrypted fallback.
- Everything shown on the dashboard is HTML-escaped; only `http(s)` links from outside data are kept.
- Feedback is accepted only from people in your `config/people.json`, and treated as preferences, never as instructions.

## Development

```bash
git clone https://github.com/alexdiehl0/position-watch-engine && cd position-watch-engine
pip install -e ".[dev]"
pytest                                   # synthetic fixtures only: no network, no keys, no cost
ruff check src tests && ruff format src tests
```

Work on a branch, open a pull request, and merge once CI is green; `main` should always be safe to run, since portfolio repositories install it by default. Cut a release by tagging (`git tag v0.4.1 && git push --tags`); a portfolio repository can pin it with the `ENGINE_REF` variable.

To try changes against a real portfolio, run the commands from that portfolio's folder (it finds `config/people.json` in the current directory), or point `POSITION_WATCH_WORKSPACE` at it.
