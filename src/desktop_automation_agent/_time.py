from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a naive UTC datetime without relying on deprecated datetime.utcnow()."""

    return datetime.now(timezone.utc).replace(tzinfo=None)
