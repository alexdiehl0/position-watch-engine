from position_watch.dashboard import render


def test_page_has_every_section_and_the_note():
    page = render.build_page()
    assert page.startswith("<!DOCTYPE html>")
    for section in (
        "overview",
        "allocation",
        "positions",
        "sectors",
        "suggestions",
        "etfs",
        "watchlist",
        "pool",
        "news",
        "excluded",
        "profile",
        "feedback",
    ):
        assert f'id="{section}"' in page
    assert "Not financial advice." in page
    assert "<h1>AI STOCK ANALYST</h1>" in page and "<title>AI Stock Analyst</title>" in page


def test_outside_text_is_escaped_and_bad_links_are_neutralised():
    page = render.build_page()
    assert "<script>alert('x')</script>" not in page
    assert "&lt;script&gt;" in page
    assert 'href="javascript:' not in page
    assert "Normal headline &amp; more" in page


def test_values_render_formatted():
    page = render.build_page()
    assert "Top up" in page  # ETF action label
    assert "42%" in page and "high" in page  # volatility with its band as a word
    assert "Left out: market cap under $2B" in page
    assert "−" in page or "+" in page


def test_fragment_has_no_document_shell():
    fragment = render.build_fragment()
    assert "<!DOCTYPE" not in fragment
    assert "Private snapshot" in fragment


def test_write_site(workspace):
    path = render.write_site()
    assert path == workspace / "site" / "index.html"
    assert path.read_text().startswith("<!DOCTYPE html>")
