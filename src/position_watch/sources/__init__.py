"""Thin API clients. Each call returns (data, error) and never estimates a value."""

from position_watch import settings as _settings  # noqa: F401  (loads .env before any client reads a key)
