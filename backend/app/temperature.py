"""Parsing and adjudication of Celsius temperature zones.

Batches historically stored their zone as free text (``"2–8°C"``). The ledger
now keeps numeric bounds; this module is the single place that understands the
legacy notation and decides whether a reading is inside the range, where the
boundary values themselves count as normal.
"""

from __future__ import annotations

import re

# A celsius range: two numbers separated by an ASCII/en dash/em dash/hyphen with
# a tilde also accepted (Chinese lab notes use both styles). Numbers may carry a
# decimal comma or point; an optional "°C / ℃ / 度" suffix is ignored.
_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_ZONE_RE = re.compile(
    rf"^\s*(?P<low>{_NUMBER})\s*(?:–|—|-|~|～|−|至)\s*(?P<high>{_NUMBER})\s*(?:°?\s*[Cc℃]|度)?\s*$"
)

# Extreme but still plausible celsius readings for a specimen ledger; purely a
# guard against misplaced values (e.g. fahrenheit or kelvin typed by mistake).
MIN_PLAUSIBLE_C = -100.0
MAX_PLAUSIBLE_C = 100.0


def parse_temperature_zone(zone: str) -> tuple[float, float]:
    """Parse legacy zone text into ``(low, high)`` celsius bounds.

    Raises ``ValueError`` when the text cannot be recognised unambiguously;
    callers (API validation and the data migration) must surface that failure
    instead of guessing bounds.
    """
    match = _ZONE_RE.match(zone)
    if not match:
        raise ValueError(f"temperature zone {zone!r} is not a celsius range")
    low = float(match.group("low").replace(",", "."))
    high = float(match.group("high").replace(",", "."))
    if low > high:
        raise ValueError(f"temperature zone {zone!r} has its lower bound above its upper bound")
    return low, high


def format_temperature_zone(low: float, high: float) -> str:
    """Canonical legacy-style label, kept for reads and the batch_created event."""
    return f"{_fmt(low)}–{_fmt(high)}°C"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def classify_temperature(value: float, low: float, high: float) -> str:
    """Boundary values are normal; only strictly outside readings are out of range."""
    return "normal" if low <= value <= high else "out_of_range"
