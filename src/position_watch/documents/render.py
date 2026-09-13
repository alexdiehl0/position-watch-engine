"""Renders the daily report (Markdown) and email (plain text) from the day's
evidence and the model's calls. Plain-text templates, so no HTML escaping;
company names, sectors, descriptions and risk lines come straight from the
evidence, and only the calls and reasoning come from the model."""

from jinja2 import Environment, PackageLoader, StrictUndefined

from position_watch import compliance


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("position_watch.documents", "templates"),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals.update(label=compliance.label, note=compliance.NOTE)
    return env


def report(**ctx) -> str:
    return _env().get_template("report.md").render(**ctx)


def email_body(**ctx) -> str:
    return _env().get_template("email.txt").render(**ctx)


def render_template(name: str, **ctx) -> str:
    return _env().get_template(name).render(**ctx)
