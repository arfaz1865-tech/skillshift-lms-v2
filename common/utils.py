"""Common utility functions"""
import datetime
from typing import Optional


def parse_dob(value: Optional[str]) -> Optional[datetime.datetime]:
    """Parse date of birth from ISO format string"""
    if value is None:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError("Invalid dob format. Expected ISO date or datetime.") from e
