"""`position-watch init DIR`: a new, ready-to-fill portfolio repository.

Copies the scaffold -- example config, empty holdings files, the daily and
monthly workflows, README, .gitignore and .env.example -- into DIR. It never
overwrites an existing file.
"""

from importlib import resources
from pathlib import Path

# Scaffold file -> where it goes in the new repository.
RENAMES = {
    "gitignore": ".gitignore",
    "env.example": ".env.example",
    "workflows/daily.yml": ".github/workflows/daily.yml",
    "workflows/monthly.yml": ".github/workflows/monthly.yml",
}


def _files(folder, prefix=""):
    for item in folder.iterdir():
        name = f"{prefix}{item.name}"
        if item.is_dir():
            yield from _files(item, f"{name}/")
        elif item.name not in ("__init__.py",) and not name.endswith(".pyc"):
            yield name, item


def create(target) -> list:
    target = Path(target)
    written = []
    for name, source in sorted(_files(resources.files("position_watch.scaffold"))):
        dest = target / RENAMES.get(name, name)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        written.append(dest.relative_to(target).as_posix())
    for folder in ("state", "reports", "site"):
        (target / folder).mkdir(exist_ok=True)
    return written
