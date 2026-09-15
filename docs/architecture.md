# Architecture

How Position Watch works end to end. Each portfolio has its own private repository (created with `position-watch init`) holding its data and two workflows; the engine — this repository — is installed by those workflows at a pinned release (`ENGINE_REF`) or `main`. Plain code does every step; a single Claude Opus 5 call a day turns the evidence into one call per holding. Results go out as a colour-coded email and an encrypted dashboard; replies come back through Gmail the next morning, and lasting preferences they state are written back to `config/people.json`.

## At a glance

Thick arrows reach people; dotted arrows carry secrets, feedback or the failure alert.

```mermaid
flowchart TB
  subgraph ENGINE["position-watch-engine · public · code only"]
    PKG["position-watch<br/>a tagged release · tests + CI on every change"]
  end

  subgraph REPO["Your portfolio repo · private"]
    CRON["GitHub Actions<br/>daily 04:17 UTC, two backups · monthly on the 1st"]
    SEC[("Secrets<br/>Anthropic · FMP · Finnhub<br/>Gmail · passcode · Pages token")]
    IN["config/ · data/<br/>holdings · people and preferences<br/>yesterday's handoff"]
    OUT["state/ · reports/<br/>calls · history · handoff · cost log"]
  end

  subgraph RUN["The daily run · plain code except step 3"]
    S1["1 · read yesterday's notes<br/>and replies from known senders"]
    S2["2 · gather evidence<br/>every figure tagged with its source"]
    S3["3 · ONE Claude Opus 5 call<br/>batched at half price · checked by code"]
    S4["4 · report · dashboard<br/>email once the page is live"]
    S1 --> S2 --> S3 --> S4
  end

  MK[("Market data · free<br/>FMP → Finnhub → Yahoo<br/>ECB exchange rates")]
  PAGES["Your dashboard repo · public<br/>GitHub Pages · encrypted page only"]
  MAIL["Gmail<br/>AI STOCK PORTFOLIO REVIEW — date"]
  OP(["Operator"])
  OWN(["Portfolio owner"])

  CRON -->|"installs"| PKG
  PKG -->|"runs"| S1
  SEC -.->|"read at run time, never printed"| RUN
  IN --> S1
  S2 <-->|"live calls"| MK
  S4 -->|"commits"| OUT
  S4 ==>|"encrypt · push"| PAGES
  S4 ==>|"send"| MAIL
  MAIL ==> OP & OWN
  OWN ==>|"Open dashboard button<br/>private link, key after #"| PAGES
  OWN -.->|"reply = feedback"| MAIL
  MAIL -.->|"read by tomorrow's run"| S1
  RUN -.->|"a step fails: FAILED email"| OP
```

The operator and the owner can be the same person. The monthly workflow (not drawn) runs `position-watch monthly`: the month's calls and a review of the portfolio as a whole, from the same files and one more Claude call, emailed on the 1st.

## The daily run in detail

Line styles: **thick** arrows reach people, **dotted** arrows carry secrets (never printed; redacted from any error text) or the failure alert, plain arrows are internal calls and file writes.

```mermaid
%%{init: {"flowchart": {"useMaxWidth": false, "nodeSpacing": 34, "rankSpacing": 54}}}%%
flowchart TB
  subgraph PRIV["GitHub · your portfolio repo (private) · data + workflows"]
    CRON["Actions cron · daily.yml<br/>04:17 UTC, two backups · installs the engine"] -->|"runs"| DAILY["daily.py<br/>position-watch daily"]
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
    RND --> PUB["publish.py<br/>AES-256-GCM · opens by passcode or email link"]
  end

  subgraph OUT["Delivery"]
    SMTP["Gmail · SMTP<br/>colour-coded daily email"]
    CLIENT(["Owner · operator"])
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
  PUB ==>|"push with Pages token<br/>then wait until it is live"| PAGES
  CALLS ==>|"summary line · calls · private link"| SMTP
  SMTP ==> CLIENT
  CLIENT ==>|"Open dashboard button<br/>or passcode"| PAGES
  CLIENT ==>|"replies = feedback"| NEXT
  DAILY -.->|"a step fails: FAILED email<br/>to the operator only"| SMTP
```

## Shipping a change

```mermaid
flowchart LR
  A["Edit on a branch"] --> B["Pull request"] --> C["CI · ruff + pytest"] --> D["Merge to main"] --> E["Tag vX.Y.Z"] --> F["ENGINE_REF in the portfolio repo"] --> G["Next run uses it"]
```

Portfolio repos that don't set `ENGINE_REF` install `main`, which is why `main` must always run.
