# Getting started: your own portfolio

This sets up Position Watch for **your** portfolio, on **your** accounts: your data stays in your own private repository, and the costs (about $5–10 a month of Claude usage, everything else free) are billed to you. Allow about 20 minutes. You'll need a computer with Python 3.10 or newer, and the `gh` command (GitHub's command-line tool, from cli.github.com).

## 1. Accounts and keys

| What | Where | Used for |
|---|---|---|
| GitHub account | github.com | Your private portfolio repository and the daily schedule |
| Anthropic API key | console.anthropic.com → API keys (add a payment method) | The daily and monthly Claude calls |
| Finnhub key | finnhub.io (free) | Ratios, news, the watchlist screen |
| Financial Modeling Prep key | financialmodelingprep.com (free) | Extra fundamentals for large stocks |
| Gmail app password | myaccount.google.com → Security → 2-Step Verification → App passwords | Sending your daily email and reading your replies |

Keep the keys in a password manager. Never paste them into a chat, an email or an issue.

## 2. Create your portfolio repository

```bash
pip install "position-watch @ git+https://github.com/alexdiehl0/position-watch-engine"
gh auth login
position-watch init my-portfolio
cd my-portfolio
git init -b main && git add -A && git commit -m "My portfolio"
gh repo create my-portfolio --private --source . --push
```

## 3. Add your data

- **`data/processed/holdings.csv`** — one row per position, columns explained in your repository's `README.md`. Easiest: export your positions from your broker and copy the numbers across. Save the original export in `data/raw/`.
- **`config/people.json`** — your name and email, and your preferences: time horizon, goal, risk tolerance, what you favour and what to avoid. Add anyone else who should get the daily email.
- **`config/instruments.json`** — list your ETFs (`"type": "etf"`) and anything that isn't a stock (`"type": "commodity"`), plus the Yahoo ticker for any listing outside the US (London ETFs end in `.L`, Paris shares in `.PA`).

Then `git add -A && git commit -m "My holdings" && git push`.

## 4. Add your secrets

From inside `my-portfolio` (each command asks for the value without showing it):

```bash
gh secret set ANTHROPIC_API_KEY
gh secret set FINNHUB_API_KEY
gh secret set FMP_API_KEY
gh secret set MAIL_USER          # your Gmail address
gh secret set MAIL_APP_PASSWORD  # the app password from step 1
```

## 5. First run

```bash
gh workflow run daily.yml
gh run watch
```

A few minutes later — the Claude call is batched at half price, so it can take a little longer — your first "Portfolio Review" email arrives. From then on it runs every morning at 04:00 UTC, and the monthly review on the 1st. Reply to any email to tell the next run what to weigh differently.

## Optional: the locked dashboard

The email is complete on its own; the dashboard adds charts and tables you can open on your phone.

1. Create a public repository for it: `gh repo create my-dashboard --public --add-readme`, then in its Settings → Pages, deploy from the `main` branch.
2. Create a fine-grained token (GitHub → Settings → Developer settings → Fine-grained tokens) with access to that repository only and **Contents: read and write**.
3. In your portfolio repository:
   ```bash
   gh secret set PAGES_TOKEN
   gh secret set DASHBOARD_PASSCODE   # a long phrase, not a short PIN
   gh variable set PAGES_REPOSITORY --body "<your-github-name>/my-dashboard"
   ```

The page is encrypted before it's published: anyone can reach the address, but only the button in your daily email (a private link) or the passcode opens it. Don't forward the email; changing the passcode invalidates old links.

## Keeping up to date

Your workflows install the latest engine every run, so improvements arrive by themselves. To stay on a fixed version instead, set `gh variable set ENGINE_REF --body v0.4.0`, and change it when you're ready to upgrade.
