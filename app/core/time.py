"""Explicit Singapore calendar helpers used by deterministic business rules."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

SINGAPORE_TIMEZONE = ZoneInfo("Asia/Singapore")


def singapore_date(reference_datetime: datetime) -> date:
    """Return the Singapore calendar date for an aware reference datetime."""
    if reference_datetime.tzinfo is None or reference_datetime.utcoffset() is None:
        raise ValueError("reference_datetime must be timezone-aware")
    return reference_datetime.astimezone(SINGAPORE_TIMEZONE).date()


def add_calendar_month(value: date) -> date:
    """Add one calendar month, clamping to the target month's final day."""
    return value + relativedelta(months=1)
