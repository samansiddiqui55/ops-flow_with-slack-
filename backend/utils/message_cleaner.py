"""
CHANGE 6: Clean operational summary for the dashboard "Full Message" panel.

Removes raw email metadata (From:/To:/Cc:/Subject: headers, mailto links,
HTML residue, signatures, "view in browser" footers) and produces a short,
human-readable text the dashboard can render.

The original `full_message` is NEVER mutated in storage; this function is
only used to build a `display_message` field at serialize time.
"""
from __future__ import annotations

import re
from typing import Optional


_HEADER_PREFIXES = (
    "from:", "to:", "cc:", "bcc:", "subject:", "date:", "sent:",
    "reply-to:", "return-path:", "delivered-to:", "received:", "x-",
    "content-type:", "mime-version:", "message-id:", "in-reply-to:",
    "references:", "user-agent:", "thread-index:", "thread-topic:",
    "list-unsubscribe:", "precedence:",
)

_SIGNATURE_BOUNDARIES = (
    "-- ", "--\n", "best regards", "kind regards", "regards,",
    "thanks,", "thank you,", "sent from my", "get outlook for",
    "this email is confidential",
)

_NOISE_PATTERNS = [
    # mailto: links
    re.compile(r"mailto:[\S]+", re.IGNORECASE),
    # bare urls (keep first 80 chars only)
    re.compile(r"https?://\S{80,}"),
    # angle-bracket emails (e.g. "<foo@bar.com>")
    re.compile(r"<\s*[\w\.\-+]+@[\w\.\-]+\s*>"),
    # standalone "view this email in your browser" footers
    re.compile(r"view\s+this\s+email\s+in\s+your\s+browser.*", re.IGNORECASE),
    re.compile(r"unsubscribe\s+from\s+this\s+list.*", re.IGNORECASE),
    re.compile(r"manage\s+email\s+preferences.*", re.IGNORECASE),
    # excessive whitespace runs
    re.compile(r"[ \t]{3,}"),
]


def _strip_html(text: str) -> str:
    if "<" not in text:
        return text
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&amp;", "&")
                .replace("&quot;", '"')
                .replace("&#39;", "'"))
    return text


def _drop_email_headers(text: str) -> str:
    """Strip leading lines that look like RFC822 headers."""
    out = []
    in_header_block = True
    for line in text.split("\n"):
        if in_header_block:
            stripped = line.strip().lower()
            if not stripped:
                # blank line ends header block
                in_header_block = False
                continue
            if stripped.startswith(_HEADER_PREFIXES):
                continue
            # If first non-empty line doesn't look like a header, exit header mode
            in_header_block = False
        out.append(line)
    return "\n".join(out)


def _drop_quoted_replies(text: str) -> str:
    """Remove quoted reply blocks ('> ' prefixed lines) and 'On <date>, X wrote:' fences."""
    lines = text.split("\n")
    kept = []
    skipping = False
    for line in lines:
        s = line.strip()
        if not skipping and (
            re.match(r"^on\s+.+wrote:\s*$", s, re.IGNORECASE)
            or re.match(r"^-{3,}\s*original message\s*-{3,}", s, re.IGNORECASE)
            or re.match(r"^_+\s*$", s)
        ):
            skipping = True
            continue
        if skipping and s.startswith(">"):
            continue
        # any '> ' line at top-level is a quoted reply: drop
        if s.startswith(">"):
            continue
        skipping = False
        kept.append(line)
    return "\n".join(kept)


def _drop_signature(text: str) -> str:
    lower = text.lower()
    for boundary in _SIGNATURE_BOUNDARIES:
        idx = lower.find(boundary)
        if idx > 80:  # only trim if there's enough body before the signature
            return text[:idx].rstrip()
    return text


def _apply_noise_patterns(text: str) -> str:
    for pat in _NOISE_PATTERNS:
        text = pat.sub(" ", text)
    return text


def build_display_message(full_message: Optional[str], max_chars: int = 1200) -> str:
    """
    Build a clean human summary from a raw email/Slack body.
    Returns at most `max_chars` characters (sentence-aware trim).
    """
    if not full_message:
        return ""

    text = full_message
    text = _strip_html(text)
    text = _drop_email_headers(text)
    text = _drop_quoted_replies(text)
    text = _apply_noise_patterns(text)
    text = _drop_signature(text)

    # Collapse 3+ newlines to 2 and 2+ spaces to 1
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()

    if not text:
        return ""

    if len(text) > max_chars:
        cut = text[:max_chars]
        # try to break at sentence end
        last_dot = max(cut.rfind(". "), cut.rfind(".\n"))
        if last_dot > max_chars * 0.6:
            cut = cut[: last_dot + 1]
        text = cut.rstrip() + "…"

    return text
