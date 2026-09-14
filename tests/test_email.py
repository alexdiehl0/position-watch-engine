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
