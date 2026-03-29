"""Canonical connection_type helpers for node network policies."""
from __future__ import annotations

from typing import Iterable, List, Tuple


# Node policy allowed values (as defined in product spec).
VALID_CONNECTION_TYPES: set[str] = {
    "mobile",
    "mobile_isp",
    "fixed",
    "isp",
    "regional_isp",
    "residential",
    "hosting",
    "vpn",
    "business",
}


def normalize_connection_type(value: str | None) -> str | None:
    """Normalize a single connection type string to lowercase canonical form."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    aliases = {
        # GeoIP providers may return these non-canonical labels.
        "datacenter": "hosting",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized or None


def normalize_connection_types(values: Iterable[str] | None) -> Tuple[List[str], List[str]]:
    """
    Normalize list of connection types preserving order, removing duplicates.

    Returns:
        (normalized_valid_values, invalid_values)
    """
    if values is None:
        return ([], [])

    normalized: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    for raw in values:
        value = normalize_connection_type(raw)
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        if value in VALID_CONNECTION_TYPES:
            normalized.append(value)
        else:
            invalid.append(value)

    return (normalized, invalid)
