# Monthly Portfolio Review — {{ month }}

A look back over the past month, computed from this portfolio's own records (no new market data), with a review of the portfolio as a whole by {{ model }}. Figures are as of the latest daily run.

## The portfolio as a whole

{{ answer.overview }}

| Measure | Value |
|---|---|
| Value | ${{ "{:,.0f}".format(snap.totals.value_usd) }} |
| Return on cost | ${{ "{:,.0f}".format(snap.totals.pnl_usd) }} ({{ "%.1f" | format(snap.totals.pnl_pct or 0) }}%) |
| Dividends received | ${{ "{:,.0f}".format(snap.income.dividends_received_usd) }}{% if snap.income.dividends_on_cost_pct is not none %} ({{ snap.income.dividends_on_cost_pct }}% of cost){% endif %} |
| Largest position | {% if snap.concentration.largest %}{{ snap.concentration.largest.symbol }} ({{ snap.concentration.largest.weight_pct }}%){% else %}–{% endif %} |
| Top 3 positions | {{ snap.concentration.top3_weight_pct }}% |
| Effective number of positions | {{ snap.concentration.effective_positions }} |
| Volatility (value-weighted, 3-month) | {% if snap.risk.volatility_3m_pct.value is not none %}{{ snap.risk.volatility_3m_pct.value }}% across {{ snap.risk.volatility_3m_pct.coverage_pct }}% of the portfolio{% else %}not available{% endif %} |
| Beta (value-weighted) | {% if snap.risk.beta.value is not none %}{{ snap.risk.beta.value }} across {{ snap.risk.beta.coverage_pct }}% of the portfolio{% else %}not available{% endif %} |
{% if snap.get("since_last_month") %}
| Change since {{ snap.since_last_month.previous_month }} | value {{ "{:+,.0f}".format(snap.since_last_month.value_change_usd) }} USD, dividends {{ "{:+,.0f}".format(snap.since_last_month.dividends_change_usd) }} USD |
{% endif %}

**Sectors:** {% for s in snap.sectors %}{{ s.sector }} {{ s.share_pct }}%{% if not loop.last %} · {% endif %}{% endfor %}

**Currencies:** {% for c, v in snap.currency_pct.items() %}{{ c }} {{ v }}%{% if not loop.last %} · {% endif %}{% endfor %}

**Asset mix:** {% for k, v in snap.asset_mix_pct.items() %}{{ k }} {{ v }}%{% if not loop.last %} · {% endif %}{% endfor %}

### Strengths

{% for s in answer.strengths %}
- {{ s }}
{% endfor %}

### Risks

{% for s in answer.risks %}
- {{ s }}
{% endfor %}

### Worth considering

{% for s in answer.to_consider %}
- {{ s }}
{% endfor %}

## How the calls evolved

{% for s in answer.call_patterns %}
- {{ s }}
{% endfor %}

| Symbol | Kind | Days | Buy / Add / Top up | Hold | Trim / Sell | Changes | Latest |
|---|---|---|---|---|---|---|---|
{% for h in history %}
| {{ h.symbol }} | {{ h.scope }} | {{ h.days }} | {{ h.positive }} | {{ h.hold }} | {{ h.negative }} | {% for c in h.changes %}{{ c.date }}: {{ c.from }} → {{ c.to }}{% if not loop.last %}; {% endif %}{% else %}none{% endfor %} | {{ h.latest.action }} ({{ h.latest.date }}) |
{% endfor %}

---

{{ note }}
