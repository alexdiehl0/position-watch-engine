"""Renders the dashboard from templates/ and static/ with the view model.

The output is one self-contained HTML file (CSS and JS inlined), so the same
page works as the site file, inside the encrypted GitHub Pages copy and as a
Claude Artifact. Jinja autoescaping is on for every value; the only markup
marked safe is our own CSS and JS read from static/.

    build_page()      full HTML document
    build_fragment()  body-only version for a Claude Artifact
    write_site()      writes the full page to the workspace's site/index.html
"""

from importlib import resources
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from position_watch import settings
from position_watch.dashboard import view

CURRENCY_SIGN = {"USD": "$", "EUR": "€"}
DASH, MINUS = "–", "−"


# ---- Formatting filters (return plain text; Jinja escapes it) ------------


def money(value, currency="USD", decimals=0, signed=False):
    if value is None:
        return DASH
    sign = "+" if signed and value > 0 else (MINUS if value < 0 else "")
    return f"{sign}{CURRENCY_SIGN.get(currency, currency + ' ')}{abs(value):,.{decimals}f}"


def pct(value, plus=True):
    if value is None:
        return DASH
    sign = ("+" if plus else "") if value > 0 else (MINUS if value < 0 else "")
    return f"{sign}{abs(value):.1f}%"


def fmt(value, spec):
    return DASH if value is None else spec.format(value)


def tone(value):
    return "good" if (value or 0) > 0 else ("crit" if (value or 0) < 0 else "")


def safe_url(url):
    """Only http(s) links from outside data; anything else (javascript:, data:) becomes inert."""
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else "#"


def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("position_watch.dashboard", "templates"),
        autoescape=select_autoescape(["html"], default_for_string=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(money=money, pct=pct, fmt=fmt, tone=tone, safe_url=safe_url)
    return env


def _static(name: str) -> Markup:
    return Markup(resources.files("position_watch.dashboard").joinpath("static", name).read_text())


def _render(template: str, fragment: bool) -> str:
    ctx = view.build(fragment=fragment)
    return _environment().get_template(template).render(css=_static("style.css"), js=_static("app.js"), **ctx)


def build_page() -> str:
    return _render("page.html", fragment=False)


def build_fragment() -> str:
    return _render("fragment.html", fragment=True)


def write_site(path=None) -> Path:
    out = Path(path) if path else settings.site_dir() / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page())
    return out
