"""Where things live, and the environment.

The engine (this package) and a portfolio's data live apart. Everything about
one portfolio -- configuration, broker data, run state, reports and the built
dashboard -- sits in one *workspace* folder, normally the root of that
portfolio's own private repository (created with `position-watch init`).

The workspace is, in order: POSITION_WATCH_WORKSPACE if set; otherwise the
current directory if it holds config/people.json; otherwise ./workspace.
`.env` is read from the current directory. Paths are functions, so a change
to the environment (e.g. in a test) takes effect immediately.

Links for the email and dashboard come from the environment too, so no
repository name is written into the code: POSITION_WATCH_REPORTS_URL and
POSITION_WATCH_DASHBOARD_URL, or -- inside GitHub Actions -- the repository
the run belongs to and the PAGES_REPOSITORY variable.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")

# Secrets. Values are only ever read from the environment, never printed
# (see `python -m position_watch check-env`). Market data is needed by every
# run; the rest only by `daily` (Claude call, email) and publishing.
REQUIRED_SECRETS = ("FMP_API_KEY", "FINNHUB_API_KEY")
OPTIONAL_SECRETS = ("ANTHROPIC_API_KEY", "MAIL_USER", "MAIL_APP_PASSWORD", "DASHBOARD_PASSCODE")


def workspace() -> Path:
    if os.environ.get("POSITION_WATCH_WORKSPACE"):
        return Path(os.environ["POSITION_WATCH_WORKSPACE"])
    cwd = Path.cwd()
    return cwd if (cwd / "config" / "people.json").exists() else cwd / "workspace"


def config_dir() -> Path:
    return workspace() / "config"


def data_dir() -> Path:
    return workspace() / "data"


def state_dir() -> Path:
    return workspace() / "state"


def reports_dir() -> Path:
    return workspace() / "reports"


def site_dir() -> Path:
    return workspace() / "site"


def transactions_csv() -> Path:
    return data_dir() / "processed" / "transactions.csv"


def holdings_csv() -> Path:
    return data_dir() / "processed" / "holdings.csv"


def latest_review_path() -> Path:
    return state_dir() / "latest_review.json"


def suggestions_path() -> Path:
    return state_dir() / "suggestions.json"


def reports_url() -> str | None:
    """Web address of the reports folder, or None when there isn't one."""
    if os.environ.get("POSITION_WATCH_REPORTS_URL"):
        return os.environ["POSITION_WATCH_REPORTS_URL"].rstrip("/")
    repo, checkout = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_WORKSPACE")
    if not repo or not checkout:
        return None
    try:
        folder = reports_dir().resolve().relative_to(Path(checkout).resolve()).as_posix()
    except ValueError:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{repo}/tree/{os.environ.get('GITHUB_REF_NAME', 'main')}/{folder}"


def report_url(day: str) -> str | None:
    base = reports_url()
    return base.replace("/tree/", "/blob/", 1) + f"/{day}-review.md" if base else None


def dashboard_url() -> str | None:
    """Address of the locked dashboard: explicit, or the GitHub Pages site of PAGES_REPOSITORY (owner/name)."""
    if os.environ.get("POSITION_WATCH_DASHBOARD_URL"):
        return os.environ["POSITION_WATCH_DASHBOARD_URL"]
    pages = os.environ.get("PAGES_REPOSITORY", "")
    if "/" not in pages:
        return None
    owner, name = pages.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def redact(text) -> str:
    """Removes every secret's value from a message (e.g. an API key inside a
    failed request's URL) before it can reach a log, report or email."""
    text = str(text)
    for name in REQUIRED_SECRETS + OPTIONAL_SECRETS:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return text


def secret_status() -> dict:
    """{name: True/False} for each secret -- whether it is set, never its value."""
    return {name: bool(os.environ.get(name)) for name in REQUIRED_SECRETS + OPTIONAL_SECRETS}
