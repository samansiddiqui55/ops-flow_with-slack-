from datetime import datetime
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


def format_jira_description_from_email(
    sender_name: Optional[str],
    sender_email: str,
    brand: str,
    subject: str,
    body: str,
    cc_emails: list,
    timestamp: datetime,
) -> str:
    """Format email data into Jira issue description."""
    return f"""**Email Issue Report**

**From:** {sender_name or 'Unknown'} <{sender_email}>
**Brand:** {brand}
**Original Subject:** {subject}
**Received:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

{('**CC:** ' + ', '.join(cc_emails)) if cc_emails else ''}

---

**Issue Description:**

{body}
"""


def format_jira_description_from_slack(
    user_name: str,
    user_id: str,
    channel_name: str,
    message: str,
    extracted_ids: dict,
    tagged_users: list,
    timestamp: datetime,
) -> str:
    """Format Slack message into Jira issue description."""
    ids_text = ""
    if extracted_ids:
        ids_list = [f"- **{k.upper()}:** {v}" for k, v in extracted_ids.items() if v]
        ids_text = "\n".join(ids_list)

    return f"""**Slack Bug Report**

**Reporter:** {user_name} (ID: {user_id})
**Channel:** #{channel_name}
**Reported:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

{('**Tagged Users:** ' + ', '.join(tagged_users)) if tagged_users else ''}

**Extracted IDs:**
{ids_text if ids_text else 'No IDs extracted'}

---

**Issue Description:**

{message}
"""


def format_resolution_email(
    ticket_key: str,
    ticket_summary: str,
    resolution_comment: str,
    timestamp: datetime,
) -> tuple[str, str]:
    """Format resolution email (returns subject and body)."""
    subject = f"Resolved: {ticket_summary} | {ticket_key}"

    body = f"""Dear Customer,

We are pleased to inform you that your support issue has been resolved.

**Ticket ID:** {ticket_key}
**Issue:** {ticket_summary}
**Resolved On:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

**Resolution:**
{resolution_comment}

If you have any further questions or concerns, please don't hesitate to reach out.

Thank you for your patience.

Best regards,
OpsFlow Support Team
"""
    return subject, body


# ---------------------------------------------------------------
# Slack-first operational message formats
# ---------------------------------------------------------------

def format_slack_ticket_created(
    *,
    jira_key: Optional[str],
    jira_url: Optional[str],
    priority: str,
    issue_type: str,
    assignee: Optional[str],
    status: str,
    summary: str,
) -> str:
    """
    Slack-first operational reply when a ticket is created.

    Example:
        ✅ Ticket Created: TEC-123
        Priority: Medium
        Issue Type: Shipment Delay
        Assigned To: Aarushi
        Status: Open
        <https://...|View in Jira>
    """
    title_id = jira_key or "OpsFlow ticket"
    assignee_text = assignee or "Unassigned"
    lines = [
        f":white_check_mark: *Ticket Created:* `{title_id}`",
        f"*Priority:* {priority}",
        f"*Issue Type:* {issue_type}",
        f"*Assigned To:* {assignee_text}",
        f"*Status:* {status}",
    ]
    if summary:
        short = summary if len(summary) <= 140 else summary[:137] + "..."
        lines.append(f"*Summary:* {short}")
    if jira_url:
        lines.append(f"<{jira_url}|View in Jira>")
    return "\n".join(lines)


def format_slack_resolution_message(
    ticket_key: str,
    ticket_summary: str,
    resolution_comment: str,
    resolved_by: Optional[str] = None,
) -> str:
    """
    Slack-first resolution reply, posted in the same Slack thread.

    Example:
        ✅ Issue Resolved
        TEC-123 marked resolved by Deepak Singh

        Resolution:
        Shipment marked delivered successfully.
    """
    closer = resolved_by or "OpsFlow"
    summary_line = ticket_summary if len(ticket_summary) <= 140 else ticket_summary[:137] + "..."
    return (
        f":white_check_mark: *Issue Resolved*\n"
        f"`{ticket_key}` marked resolved by *{closer}*\n"
        f"_Issue:_ {summary_line}\n\n"
        f"*Resolution:*\n{resolution_comment}"
    )
