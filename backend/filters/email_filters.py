"""
CHANGE 2: Centralized email filter loader.

Loads /app/backend/filters/email_filters.json at import time and exposes
helpers used by the email_service to decide whether an incoming email is
operational (allowed) or junk/promotional (blocked).

Logic:
  1. Allowlist (domain or exact sender) → ALWAYS allowed
  2. Blocklist domain / sender_pattern / subject_keyword / body_keyword → blocked
  3. Default → allowed (preserve real operational/client emails)

Config can be reloaded without restart by calling reload_filters().
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "email_filters.json"

_filters: Dict[str, List[str]] = {
    "blocklist_domains": [],
    "blocklist_sender_patterns": [],
    "blocklist_subject_keywords": [],
    "blocklist_body_keywords": [],
    "allowlist_domains": [],
    "allowlist_senders": [],
}


def _normalize(values):
    return [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]


def reload_filters() -> None:
    """Reload filter lists from disk (called once at import; can be re-called)."""
    global _filters
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _filters = {
            "blocklist_domains": _normalize(raw.get("blocklist_domains", [])),
            "blocklist_sender_patterns": _normalize(raw.get("blocklist_sender_patterns", [])),
            "blocklist_subject_keywords": _normalize(raw.get("blocklist_subject_keywords", [])),
            "blocklist_body_keywords": _normalize(raw.get("blocklist_body_keywords", [])),
            "allowlist_domains": _normalize(raw.get("allowlist_domains", [])),
            "allowlist_senders": _normalize(raw.get("allowlist_senders", [])),
        }
        logger.info(
            f"[EMAIL-FILTER] loaded: "
            f"block_domains={len(_filters['blocklist_domains'])} "
            f"block_senders={len(_filters['blocklist_sender_patterns'])} "
            f"block_subjects={len(_filters['blocklist_subject_keywords'])} "
            f"allow_domains={len(_filters['allowlist_domains'])} "
            f"allow_senders={len(_filters['allowlist_senders'])}"
        )
    except FileNotFoundError:
        logger.warning(f"[EMAIL-FILTER] config not found at {CONFIG_PATH} - using defaults")
    except Exception as e:
        logger.error(f"[EMAIL-FILTER] failed to load config: {e}", exc_info=True)


def get_filters() -> Dict[str, List[str]]:
    return _filters


def _sender_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def is_allowlisted(sender_email: str) -> bool:
    """Allowlist always wins. Exact sender or trailing-domain match."""
    e = (sender_email or "").strip().lower()
    if not e:
        return False
    if e in _filters["allowlist_senders"]:
        return True
    domain = _sender_domain(e)
    if not domain:
        return False
    for d in _filters["allowlist_domains"]:
        if domain == d or domain.endswith("." + d):
            return True
    return False


def is_blocked_sender(sender_email: str) -> tuple[bool, str]:
    e = (sender_email or "").strip().lower()
    if not e:
        return True, "empty_sender"

    domain = _sender_domain(e)
    for d in _filters["blocklist_domains"]:
        if domain == d or domain.endswith("." + d):
            return True, f"domain:{d}"

    for pat in _filters["blocklist_sender_patterns"]:
        if pat in e:
            return True, f"sender_pattern:{pat}"

    return False, ""


def is_blocked_subject(subject: str) -> tuple[bool, str]:
    s = (subject or "").lower()
    for kw in _filters["blocklist_subject_keywords"]:
        if kw in s:
            return True, f"subject:{kw}"
    return False, ""


def is_blocked_body(body: str) -> tuple[bool, str]:
    if not body:
        return False, ""
    b = body.lower()
    for kw in _filters["blocklist_body_keywords"]:
        if kw in b:
            return True, f"body:{kw}"
    return False, ""


def should_process_email(sender_email: str, subject: str, body: str = "") -> tuple[bool, str]:
    """Single entrypoint used by email_service. Returns (process, reason)."""
    if is_allowlisted(sender_email):
        return True, "allowlisted"

    blocked, why = is_blocked_sender(sender_email)
    if blocked:
        return False, why

    blocked, why = is_blocked_subject(subject)
    if blocked:
        return False, why

    blocked, why = is_blocked_body(body)
    if blocked:
        return False, why

    return True, "ok"


# Load on import
reload_filters()
