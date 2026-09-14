from position_watch.documents import render as documents

REVIEW = {"holdings": {"XSS": {"company_name": "<script>alert(1)</script> Corp"}}, "etfs": {}, "candidates": {}}
CALLS = {"holdings": [{"symbol": "XSS", "action": "sell", "one_line": "Price <b>jumped</b> & fell."}]}
TOTALS = {"value_usd": 1000.0, "pnl_usd": -50.0, "pnl_pct": -4.8, "dividends_usd": 10.0}


def test_email_escapes_outside_text_and_marks_losses():
    msg = documents.email("2026-01-02", REVIEW, CALLS, TOTALS, None, None, False)
    assert "<script>" not in msg["html"] and "&lt;script&gt;" in msg["html"]
    assert "&lt;b&gt;jumped&lt;/b&gt; &amp; fell." in msg["html"]
    assert ">SELL</span>" in msg["html"] and "#fee2e2" in msg["html"]  # red badge, with the word
    assert "−$50" in msg["text"] and "Full report" not in msg["text"]  # no link without a repository


def test_feedback_accepts_replies_to_the_new_title():
    from position_watch import mail

    assert "Re: AI STOCK PORTFOLIO REVIEW" in mail.FEEDBACK_SUBJECTS


def test_your_stocks_show_performance_and_volatility():
    review = {
        "holdings": {"USDS": {"volatility_3m_pct": 24.4}, "EURS": {}},
        "etfs": {},
        "candidates": {"AAA": {"volatility_3m_pct": 50.0}},
    }
    calls = {
        "holdings": [{"symbol": "USDS", "action": "hold", "one_line": "Fine."},
                     {"symbol": "EURS", "action": "add", "one_line": "Cheap."}],
        "candidates": [{"symbol": "AAA", "action": "buy", "one_line": "Screened."}],
    }  # fmt: skip
    positions = {"USDS": {"pnl_pct": 20.0, "currency": "USD"}, "EURS": {"pnl_pct": -3.25, "currency": "EUR"}}
    msg = documents.email("2026-01-02", review, calls, TOTALS, None, None, False, positions=positions)

    assert "HOLD  USDS — \n+20.0% since you bought · volatility 24% (moderate)\nFine." in msg["text"]
    assert "−3.2% since you bought (in EUR)\nCheap." in msg["text"]  # no volatility known: left out
    assert "volatility 50%" not in msg["text"]  # watchlist rows stay short
    assert "Volatility <span" in msg["html"] and "(in EUR)" in msg["html"]
