"""
app/utils/enum_utils.py

Universal case-insensitive enum coercion.

Allows API clients to send enum values in any case:
  "flood", "FLOOD", "Flood"  → DisasterType.FLOOD
  "admin", "ADMIN", "Admin"  → EmergencyTeamRole.ADMIN
  "close_lane", "CLOSE_LANE" → OverrideType.CLOSE_LANE

Usage in Pydantic validators:
    from app.utils.enum_utils import coerce_enum, normalize_enum_value
"""

import enum
from typing import Type, TypeVar

T = TypeVar("T", bound=enum.Enum)


def coerce_enum(enum_class: Type[T], value: str) -> T:
    """
    Convert a string to an enum member case-insensitively.

    Tries in order:
      1. Exact value match
      2. Case-insensitive value match
      3. Case-insensitive name match

    Raises ValueError with helpful message if nothing matches.
    """
    if isinstance(value, enum_class):
        return value

    # Exact match first
    try:
        return enum_class(value)
    except ValueError:
        pass

    value_lower = value.lower()

    # Case-insensitive value match
    for member in enum_class:
        if member.value.lower() == value_lower:
            return member

    # Case-insensitive name match
    for member in enum_class:
        if member.name.lower() == value_lower:
            return member

    valid = [m.value for m in enum_class]
    raise ValueError(
        f"'{value}' is not a valid {enum_class.__name__}. "
        f"Valid values (case-insensitive): {valid}"
    )


def normalize_enum_value(enum_class: Type[T], value: str) -> str:
    """
    Normalize a string to the canonical enum .value string.
    Useful for Pydantic validators that return strings.
    """
    return coerce_enum(enum_class, value).value