{% macro stock_section(c, d) %}
### {{ c.symbol }} — {{ d.get("company_name") or "not returned" }}

*{{ d.get("sector") or "Sector not returned" }}{% if d.get("industry") %} · {{ d.industry }}{% endif %}*

{{ d.get("description") or "Description: not returned." }}

{% if d.get("pool") %}
**Why it's on the watchlist:** {{ "Top of today's screen" if d.pool.slot == "top" else "Rotation pick" }} — {{ d.pool.screen }} [Finnhub]

{% endif %}
- **Action:** {{ label(c.action) }}
- **Risk:** {{ d.get("volatility_note") or "not returned" }}

{{ c.reasoning }}

- **Data gaps:** {{ (d.get("data_gaps") or []) | join("; ") or "none" }}

{% endmacro %}
# Daily Portfolio Review — {{ date }}

Every figure below comes from live FMP, Finnhub and yfinance calls made during this run ({{ run.started_at }} to {{ run.finished_at }}), tagged with the source that returned it. The calls were made by {{ model }} from that evidence only.

**Portfolio:** {{ summary_line }}

**Carried over:** {{ calls.context_note }}

{% if notes %}
{% for n in notes %}
**Note:** {{ n }}
{% endfor %}
{% endif %}

{% if markets.snapshot or markets.briefing %}
## Markets & world

{% if markets.snapshot %}
| Market | Level | 1 day | 5 days |
|---|---|---|---|
{% for r in markets.snapshot %}
| {{ r.name }} | {{ r.level_text }} | {{ r.d1 }} | {{ r.d5 }} |
{% endfor %}

Last closes as of each market's own date [yfinance]; yields move in basis points.

{% endif %}
{% for b in markets.briefing %}
- **{{ b.development }}** {{ b.impact }} *Affects: {{ b.affects | join(", ") or "none named" }}.* Sources: {% for h in b.sources %}[{{ h.id }} {{ h.source }}]({{ h.url }}){{ ", " if not loop.last }}{% endfor %}

{% endfor %}

{% endif %}

## Current Holdings

{% for c in calls.holdings %}
{{ stock_section(c, review.holdings[c.symbol]) }}
{% endfor %}
## Core ETFs

{% for c in calls.etfs %}
{% set e = review.etfs[c.symbol] %}
### {{ c.symbol }} — {{ e.name }}

- **Action:** {{ label(c.action) }}

{{ c.reasoning }}

- **Data gaps:** {{ (e.get("data_gaps") or []) | join("; ") or "none" }}

{% endfor %}
## Candidates

{% for c in calls.candidates %}
{{ stock_section(c, review.candidates[c.symbol]) }}
{% endfor %}
## Excluded Positions

{% for e in review.excluded %}
- **{{ e.symbol }}** — {{ e.reason }}
{% else %}
None.
{% endfor %}
{% if calls.feedback_applied %}

## Feedback applied

{% for f in calls.feedback_applied %}
- {{ f.how }}
{% endfor %}
{% endif %}

---

{{ note }}
