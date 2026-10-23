
"""Timezone conversion utilities."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def to_ist(value: datetime | None) -> datetime | None:
    """Convert a timestamp to IST, treating naive database values as UTC."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(IST)
