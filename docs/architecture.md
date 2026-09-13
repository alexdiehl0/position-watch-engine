# Architecture

How a day of Position Watch works end to end. Each portfolio has its own private repository (created with `position-watch init`) holding its data and two workflows; the engine — this repository — is installed by those workflows. The daily workflow runs `position-watch daily`: plain code does every step, and a single Claude Opus 5 call turns the evidence into one call per holding. Results go out by email and as an encrypted dashboard; feedback comes back through Gmail the next morning, and lasting preferences it states are written back to `config/people.json`. The monthly workflow (not drawn) runs `position-watch monthly`: the month's calls and a review of the portfolio as a whole, from the same files and one more Claude call.

Line styles: **thick** arrows reach the client, **dotted** arrows carry secrets (never printed; redacted from any error text), plain arrows are internal calls and file writes.

```mermaid
%%{init: {"flowchart": {"useMaxWidth": false, "nodeSpacing": 34, "rankSpacing": 54}}}%%
flowchart TB
  subgraph PRIV["GitHub · your portfolio repo (private) · data + workflows"]
    CRON["Actions cron · daily.yml<br/>04:00 UTC · installs the engine"] -->|"runs"| DAILY["daily.py<br/>position-watch daily"]
    SEC[("Repository secrets<br/>Anthropic · FMP · Finnhub · Gmail<br/>passcode · Pages token")]
  end

  subgraph INPUTS["Inputs"]
    CFG["config/<br/>people · instruments · stock_universe"]
    DATA["data/processed/<br/>holdings · transactions"]
    PREV["state/<br/>yesterday's handoff"]
    INBOX["Gmail inbox · IMAP<br/>replies since yesterday, known senders only"]
  end

  subgraph EVID["Evidence pass · plain code, no AI"]
    REV["review.py"]
    REV --> STK["analysis/stock.py<br/>valuation · dividends<br/>volatility · news"]
    REV --> ETF["analysis/etf.py<br/>top-up evidence"]
    REV --> POOL["analysis/pool.py<br/>screen ~130 stocks<br/>pick 6 + 2"]
    REV --> FX["sources/fx.py"]
  end

  CTX["Context for the call<br/>preferences · yesterday's notes · feedback"]

  subgraph MKT["Market data · free tiers"]
    FMP[("FMP<br/>250 calls/day")]
    FH[("Finnhub<br/>paced under 60/min")]
    YH[("Yahoo<br/>ETFs · fallback")]
    ECB[("ECB rates<br/>api.frankfurter.dev")]
  end

  EVS["state/latest_review.json<br/>every figure tagged with its source"]

  CLAUDE["reasoning.py → Claude API<br/>Opus 5 · one structured-output call<br/>batched at half price · validated"]

  subgraph WRITTEN["Written by code from the calls, committed by the workflow"]
    CALLS["state/<br/>suggestions · handoff · history<br/>api_usage (tokens, cost)"]
    REP["reports/<br/>daily review"]
  end

  subgraph DASH["Dashboard · src/position_watch/dashboard"]
    VIEW["view.py<br/>every number on the page"] --> RND["render.py<br/>one self-contained page"]
    TPL["templates/ + static/"] --> RND
    RND --> SITE["site/index.html"]
    RND --> PUB["publish.py<br/>AES-256-GCM with the passcode"]
  end

  subgraph OUT["Delivery"]
    SMTP["Gmail · SMTP<br/>daily email"]
    CLIENT(["Client"])
    NEXT["Gmail inbox<br/>read by tomorrow's run ↺"]
    subgraph PUBREPO["GitHub · your dashboard repo (public) · only the locked page"]
      PAGES["GitHub Pages<br/>owner.github.io/dashboard-repo"]
    end
  end

  SEC -.->|"env vars · read at call time, redacted from errors"| DAILY
  DAILY -->|"gather evidence"| REV
  DATA -->|"open holdings"| REV
  CFG -->|"instruments · pool settings"| REV
  CFG -->|"preferences"| CTX
  PREV --> CTX
  INBOX --> CTX

  STK --> FMP
  STK --> FH
  STK --> YH
  ETF --> YH
  POOL -->|"screen · similar companies"| FH
  FX --> ECB

  REV -->|"writes"| EVS
  EVS -->|"trimmed digest"| CLAUDE
  CTX --> CLAUDE
  CLAUDE -->|"calls · reasoning · notes<br/>preference changes"| CALLS
  CALLS -->|"templates"| REP

  EVS --> VIEW
  CALLS --> VIEW
  PUB ==>|"push with Pages token"| PAGES
  CALLS ==>|"summary line · calls · links"| SMTP
  SMTP ==> CLIENT
  CLIENT ==>|"opens with passcode"| PAGES
  CLIENT ==>|"replies = feedback"| NEXT
```
