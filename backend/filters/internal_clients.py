"""
CHANGE 3: Internal-clients exclusion for analytics.

Defines which `brand` values are considered INTERNAL (Blitznow, bug-reporting
channels, testing brands, etc.) and must be excluded from client-facing
analytics: top client, brand frequency, issues-by-client, TAT-by-client, etc.

Public API:
  - get_internal_brand_match_stage()   -> Mongo $match clause to EXCLUDE internal
  - is_internal_brand(brand: str)      -> bool
  - reload()                            -> reload JSON config

The list is loaded from filters/internal_clients.json at import.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "internal_clients.json"

_internal: Dict[str, List[str]] = {
    "internal_brands": [],
    "internal_brand_substrings": [],
    "internal_slack_channels": [],
}


def _norm(values):
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def reload() -> None:
    global _internal
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _internal = {
            "internal_brands": _norm(raw.get("internal_brands", [])),
            "internal_brand_substrings": _norm(raw.get("internal_brand_substrings", [])),
            "internal_slack_channels": _norm(raw.get("internal_slack_channels", [])),
        }
        logger.info(
            f"[INTERNAL-CLIENTS] loaded: brands={len(_internal['internal_brands'])} "
            f"substrings={len(_internal['internal_brand_substrings'])} "
            f"channels={len(_internal['internal_slack_channels'])}"
        )
    except FileNotFoundError:
        logger.warning(f"[INTERNAL-CLIENTS] config not found at {CONFIG_PATH}")
    except Exception as e:
        logger.error(f"[INTERNAL-CLIENTS] failed to load config: {e}", exc_info=True)


def is_internal_brand(brand: str) -> bool:
    if not brand:
        return True  # Empty/None brand is considered noise
    b = brand.strip()
    bl = b.lower()
    for ib in _internal["internal_brands"]:
        if bl == ib.lower():
            return True
    for sub in _internal["internal_brand_substrings"]:
        if sub.lower() in bl:
            return True
    for ch in _internal["internal_slack_channels"]:
        if bl == ch.lower():
            return True
    return False


def get_internal_brand_regexes() -> List[str]:
    """Return regex patterns used in Mongo $not/$regex to exclude internal brands."""
    pats = []
    for ib in _internal["internal_brands"]:
        pats.append(f"^{re.escape(ib)}$")
    for sub in _internal["internal_brand_substrings"]:
        pats.append(re.escape(sub))
    for ch in _internal["internal_slack_channels"]:
        pats.append(f"^{re.escape(ch)}$")
    return pats


def get_internal_match_filter() -> dict:
    """
    Returns a Mongo filter clause to EXCLUDE internal brands.
    Combines with any existing match via `$and` if needed.
    """
    pats = get_internal_brand_regexes()
    if not pats:
        return {}
    regex = "|".join(pats)
    # Use $and so we can combine multiple conditions on `brand`
    return {
        "$and": [
            {"brand": {"$nin": [None, ""]}},
            {"brand": {"$not": {"$regex": regex, "$options": "i"}}},
        ]
    }


# Load on import
reload()
