"""Helpers for parsing timestamps returned by Famly."""

from datetime import datetime


def parse_famly_datetime(value: str) -> datetime:
    """Parse ISO-8601 timestamps consistently on every supported Python."""

    if not isinstance(value, str) or not value:
        raise ValueError("Famly timestamp must be a non-empty string")
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
