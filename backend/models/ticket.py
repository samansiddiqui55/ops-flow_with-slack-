from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone
import uuid


# Issue type categories for logistics support (derived from real ops data)
ISSUE_TYPES = [
    "Pincode Serviceability",
    "Webhook Issue",
    "Order Creation Failure",
    "Delay / TAT Issue",
    "Alias Mapping",
    "API / Integration Issue",
    "Cost Policy Issue",
    "Warehouse Issue",
    "Shipment / AWB Issue",
    "Panel / UI Issue",
    "Other"
]


class TicketCreate(BaseModel):
    brand: str
    sender_email: str
    summary: str
    full_message: str
    source: str
    awb: Optional[str] = None
    issue_type: Optional[str] = "Other"
    # Slack-specific (only set when source == "slack")
    slack_thread_ts: Optional[str] = None
    slack_channel_id: Optional[str] = None


class TicketUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    latest_comment: Optional[str] = None
    resolution_notes: Optional[str] = None
    issue_type: Optional[str] = None


class Ticket(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    brand: str
    sender_email: str
    summary: str
    full_message: str
    source: str
    awb: Optional[str] = None
    issue_type: str = "Other"

    status: str = "open"
    priority: str = "Medium"
    assigned_to: Optional[str] = None  # Changed default to None instead of "Unassigned"
    latest_comment: Optional[str] = "No comments yet."
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None

    jira_issue_key: Optional[str] = None
    jira_issue_id: Optional[str] = None
    jira_url: Optional[str] = None

    # Slack thread linkage (only set when source == "slack")
    slack_thread_ts: Optional[str] = None
    slack_channel_id: Optional[str] = None

    # TAT tracking
    resolved_at: Optional[datetime] = None
    tat_hours: Optional[float] = None  # Turnaround time in hours

    # CHANGE 1: Activity history for resolve/reopen and comment events.
    # Stored as list of dicts: {timestamp, event, message, actor}
    # Optional/backward compatible - existing tickets without this field continue to work.
    activity_history: list = Field(default_factory=list)
    reopen_count: int = 0

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def classify_issue_type(subject: str, body: str) -> str:
    """
    CHANGE 5: Improved issue type classification.

    Strategy:
      1. Use weighted, more specific keyword matches per category.
      2. Subject matches weigh more than body matches (subject is intent).
      3. Delay / TAT Issue moved LATER in priority and requires DOMAIN-SPECIFIC
         context (delay AND delivery/shipment/order/tat etc.) so generic words
         like "delay" or "pending" alone don't dominate.
      4. Returns category with highest score, or 'Other' when no signal.
      5. Optional LLM fallback (hybrid) is invoked from ticket_service when
         this returns 'Other' and ISSUE_CLASSIFY_USE_LLM=true.
    """
    subj = (subject or "").lower()
    body_l = (body or "").lower()
    text = f"{subj} {body_l}"

    # category -> list of (keyword_pattern, weight). Use simple regex via 're'.
    rules: list[tuple[str, list[tuple[str, int]]]] = [
        ("Pincode Serviceability", [
            (r"\bpincode\b", 3), (r"\bpin\s*code\b", 3),
            (r"\bnot\s*serviceable\b", 4), (r"\bnon[- ]?serviceable\b", 4),
            (r"\bserviceability\b", 4), (r"\bcoverage\b", 1),
            (r"\barea\s+not\s+covered\b", 4), (r"\bsdd\s*pincode\b", 4),
            (r"\bndd\s*pincode\b", 4), (r"\bdrop\s+pincode\b", 4),
        ]),
        ("Webhook Issue", [
            (r"\bwebhook\b", 4), (r"\bcallback\s+url\b", 4),
            (r"\bcallback\s+failure\b", 4), (r"\bhook\s+failed\b", 4),
            (r"\bwebhook\s+not\s+triggered\b", 5),
            (r"\bwebhook\s+activation\b", 5),
        ]),
        ("Order Creation Failure", [
            (r"\border\s+creation\b", 4), (r"\bcreate\s+order\b", 3),
            (r"\border\s+not\s+created\b", 5), (r"\border\s+failed\b", 4),
            (r"\bbulk\s+order\s+upload\b", 4),
            (r"\border\s+allocation\b", 3), (r"\border\s+sync\b", 3),
            (r"\bcannot\s+create\s+order\b", 5),
            (r"\border\s+booking\s+failure\b", 5),
        ]),
        ("Alias Mapping", [
            (r"\balias\s*mapping\b", 5), (r"\bwh\s*mapping\b", 4),
            (r"\bwarehouse\s+mapping\b", 4), (r"\bwrong\s+alias\b", 4),
            (r"\bincorrect\s+mapping\b", 4),
            (r"\balias\b", 2),
        ]),
        ("API / Integration Issue", [
            (r"\bapi\s+timeout\b", 5), (r"\bapi\s+error\b", 4),
            (r"\bapi\s+failure\b", 4), (r"\b500\s+error\b", 4),
            (r"\binvalid\s+token\b", 5), (r"\bintegration\s+error\b", 4),
            (r"\bauth\s+error\b", 3), (r"\bendpoint\b", 2),
            (r"\bconnection\s+error\b", 3), (r"\brate\s+limit\b", 3),
        ]),
        ("Cost Policy Issue", [
            (r"\bcost\s+policy\b", 5), (r"\brate\s+card\b", 4),
            (r"\bcommercial\s+rate\b", 4), (r"\bbilling\s+discrepancy\b", 5),
            (r"\binvoice\s+amount\b", 4), (r"\bpricing\b", 2),
            (r"\bcharges\b", 1),
        ]),
        ("Warehouse Issue", [
            (r"\bnew\s+warehouse\b", 5), (r"\bwarehouse\s+address\b", 4),
            (r"\bwarehouse\s+update\b", 4), (r"\bscanning\s+issue\b", 4),
            (r"\bwh\s+capacity\b", 4), (r"\bwarehouse\b", 2),
        ]),
        ("Shipment / AWB Issue", [
            (r"\bawb\s+not\s+generated\b", 5), (r"\bduplicate\s+awb\b", 5),
            (r"\bwrong\s+awb\b", 4), (r"\bawb\s+sync\b", 4),
            (r"\bawb\s+error\b", 4), (r"\bawb\b", 2),
            (r"\bshipment\s+not\s+created\b", 5),
            (r"\btracking\s+number\b", 3), (r"\bwaybill\b", 3),
        ]),
        ("Panel / UI Issue", [
            (r"\bunable\s+to\s+log[- ]?in\b", 5),
            (r"\blogin\s+issue\b", 5), (r"\bdashboard\s+not\s+loading\b", 5),
            (r"\bpanel\s+error\b", 4), (r"\bui\s+error\b", 4),
            (r"\bpage\s+not\s+loading\b", 4), (r"\breport\s+download\b", 3),
            (r"\bpanel\b", 2),
        ]),
        # Delay / TAT comes LAST and requires logistics context to fire
        ("Delay / TAT Issue", [
            (r"\bdelivery\s+delay\b", 5), (r"\bshipment\s+delay\b", 5),
            (r"\btat\s+exceeded\b", 5), (r"\btat\s+issue\b", 5),
            (r"\btat\s+change\b", 4), (r"\bturn[\s-]?around\s+time\b", 4),
            (r"\bshipment\s+stuck\b", 4), (r"\bnot\s+delivered\b", 3),
            (r"\bdelayed\s+(by|for)\s+\d+\s+(day|hour)", 4),
            (r"\bndr\b", 3), (r"\bstuck\s+in\s+transit\b", 4),
        ]),
    ]

    import re as _re
    best_cat, best_score = "Other", 0
    for cat, kws in rules:
        score = 0
        for pat, w in kws:
            # subject hit -> double weight (intent signal)
            if _re.search(pat, subj):
                score += w * 2
            elif _re.search(pat, body_l):
                score += w
        if score > best_score:
            best_score, best_cat = score, cat

    # Need at least a meaningful signal; else 'Other'
    if best_score < 2:
        return "Other"
    return best_cat


# CHANGE 5: hybrid LLM fallback
async def classify_issue_type_hybrid(subject: str, body: str) -> str:
    """
    Hybrid classifier: try keyword first; if 'Other' AND LLM enabled,
    fall back to Emergent LLM (Claude haiku) for a best-effort category.
    Safe: any failure returns the keyword result (or 'Other').
    """
    base = classify_issue_type(subject, body)
    if base != "Other":
        return base

    import os
    use_llm = (os.environ.get("ISSUE_CLASSIFY_USE_LLM", "false").lower() == "true")
    api_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not use_llm or not api_key:
        return base

    try:
        # Lazy import so absence doesn't break boot
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
        categories = ", ".join(ISSUE_TYPES)
        chat = LlmChat(
            api_key=api_key,
            session_id="issue-classifier",
            system_message=(
                "You are an ops-issue classifier for a logistics platform. "
                f"Pick exactly ONE category from this list: {categories}. "
                "Reply with ONLY the category name, no explanation."
            ),
        ).with_model("anthropic", "claude-haiku-4-5")
        prompt = f"Subject: {subject}\n\nBody: {body[:1500]}\n\nCategory:"
        resp = await chat.send_message(UserMessage(text=prompt))
        if not resp:
            return base
        resp_str = str(resp).strip().strip(".").strip()
        # Match returned text to known categories (case-insensitive substring)
        for c in ISSUE_TYPES:
            if c.lower() in resp_str.lower():
                return c
    except Exception:
        # Logging here would require a logger import; the caller handles fallbacks
        return base
    return base
