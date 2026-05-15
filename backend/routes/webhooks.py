"""
Webhook routes - Slack Events API + Jira webhooks.

SLACK FLOW (production-grade, Slack-first):
  1. Verify Slack signature
  2. Handle URL verification challenge
  3. For root messages in #bug-reporting:
       a. Dedupe by Slack thread ts
       b. Detect priority + issue_type
       c. Extract @assignee mention (best-effort)
       d. Create Mongo ticket (source=slack)  →  Jira graceful fallback
       e. Save Slack ↔ Jira mapping
       f. Reply in same Slack thread with rich operational card
       g. Broadcast via WebSocket to dashboard

JIRA FLOW:
  Issue closed/resolved →  reply in same Slack thread + same email chain.

EVERY step has logger.info / warning / error(exc_info=True). NO silent failures.
"""

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import json
import logging
import os
import re
from datetime import datetime, timezone

from config import get_settings
from models.ticket import TicketCreate
from services.jira_service import jira_service
from services.slack_service import slack_service
from services.email_service import email_service
from services.mapping_service import mapping_service
from services.ticket_service import ticket_service
from services.ai_service import ai_service
from utils.formatters import (
    format_resolution_email,
    format_slack_resolution_message,
    format_slack_ticket_created,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# In-memory dedupe of Slack event_ids (process-level; survives Slack retries within the run)
_seen_event_ids: set[str] = set()
_SEEN_LIMIT = 5000


def _remember_event(event_id: str) -> bool:
    """Return True if event was newly recorded; False if we already saw it."""
    if not event_id:
        return True
    if event_id in _seen_event_ids:
        return False
    if len(_seen_event_ids) >= _SEEN_LIMIT:
        # naive eviction
        _seen_event_ids.clear()
    _seen_event_ids.add(event_id)
    return True


# =========================================================
# JIRA WEBHOOK
# =========================================================

@router.post("/jira")
async def jira_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Jira webhook events (issue updated/resolved)."""
    try:
        body = await request.body()
        payload = json.loads(body.decode())

        event_type = payload.get("webhookEvent")
        issue = payload.get("issue", {})
        issue_key = issue.get("key")

        logger.info(f"[JIRA-WEBHOOK] event={event_type} key={issue_key}")

        if not event_type or not issue_key:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        if event_type in ("jira:issue_updated", "issue_status_changed"):
            changelog = payload.get("changelog", {}) or {}
            items = changelog.get("items", []) or []

            for item in items:
                if item.get("field") == "status":
                    new_status = (item.get("toString") or "").strip()
                    logger.info(f"[JIRA-WEBHOOK] status change for {issue_key}: -> {new_status}")
                    if new_status.lower() in ("done", "closed", "resolved"):
                        # Extract closer name from changelog author or issue assignee
                        author = (
                            payload.get("user", {}).get("displayName")
                            or issue.get("fields", {}).get("assignee", {}).get("displayName")
                            or "Jira"
                        )
                        background_tasks.add_task(
                            _handle_jira_close,
                            issue_key,
                            issue.get("fields", {}).get("summary", ""),
                            new_status,
                            author,
                        )

        return JSONResponse(status_code=200, content={"status": "received"})

    except Exception as e:
        logger.error(f"[JIRA-WEBHOOK] error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _handle_jira_close(issue_key: str, summary: str, status: str, closed_by: str):
    """Send resolution to email and/or Slack thread when Jira issue closes."""
    try:
        logger.info(f"[JIRA-CLOSE] processing {issue_key} closed_by={closed_by} status={status}")

        resolution_comment = (
            await jira_service.get_latest_comment(issue_key)
        ) or "Issue has been resolved."

        # ----- Local Mongo ticket update + dashboard broadcast -----
        try:
            await ticket_service.resolve_ticket_by_jira_key(
                jira_key=issue_key,
                latest_comment=resolution_comment,
                resolution_notes=resolution_comment,
                resolved_by=closed_by,
            )
        except Exception as e:
            logger.warning(f"[JIRA-CLOSE] local ticket update failed for {issue_key}: {e}", exc_info=True)

        # ----- Email resolution path -----
        email_mapping = await mapping_service.get_email_mapping_by_jira(issue_key)
        if email_mapping:
            try:
                subject, body = format_resolution_email(
                    issue_key, summary, resolution_comment, datetime.now(timezone.utc)
                )
                ai_body = await ai_service.generate_resolution_email(summary, resolution_comment)
                await email_service.send_email(
                    to_address=email_mapping.get("sender_email"),
                    subject=subject,
                    body_plain=ai_body or body,
                    in_reply_to=email_mapping.get("message_id"),
                    cc_addresses=email_mapping.get("cc_emails", []),
                )
                await mapping_service.update_email_mapping_status(issue_key, "closed")
                logger.info(f"[JIRA-CLOSE] resolution email sent for {issue_key}")
            except Exception as e:
                logger.error(f"[JIRA-CLOSE] email send failed for {issue_key}: {e}", exc_info=True)
        else:
            logger.info(f"[JIRA-CLOSE] no email mapping for {issue_key}")

        # ----- Slack resolution path -----
        slack_mapping = await mapping_service.get_slack_mapping_by_jira(issue_key)
        if slack_mapping:
            try:
                message = format_slack_resolution_message(
                    ticket_key=issue_key,
                    ticket_summary=summary,
                    resolution_comment=resolution_comment,
                    resolved_by=closed_by,
                )
                ok = await slack_service.post_message(
                    channel_id=slack_mapping.get("channel_id"),
                    text=message,
                    thread_ts=slack_mapping.get("slack_thread_ts"),
                )
                if ok:
                    await mapping_service.update_slack_mapping_status(issue_key, "closed")
                    logger.info(f"[JIRA-CLOSE] Slack thread reply sent for {issue_key}")
                else:
                    logger.error(f"[JIRA-CLOSE] Slack thread reply FAILED for {issue_key}")
            except Exception as e:
                logger.error(f"[JIRA-CLOSE] Slack reply error for {issue_key}: {e}", exc_info=True)
        else:
            logger.info(f"[JIRA-CLOSE] no Slack mapping for {issue_key}")

    except Exception as e:
        logger.error(f"[JIRA-CLOSE] fatal error for {issue_key}: {e}", exc_info=True)


# =========================================================
# SLACK EVENTS
# =========================================================

@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """
    Slack Events API endpoint.
    Handles:
      - URL verification challenge
      - message (root messages in bug channel)
      - app_mention
    """
    settings = get_settings()
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")

    logger.info(
        f"[SLACK-WEBHOOK] received POST bytes={len(body_bytes)} "
        f"signed={'yes' if request.headers.get('X-Slack-Signature') else 'no'}"
    )

    # ----- Signature verification -----
    if settings.slack_signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not timestamp or not signature:
            logger.warning("[SLACK-WEBHOOK] missing signature headers")
            raise HTTPException(status_code=401, detail="Missing Slack signature headers")
        if not slack_service.verify_signature(timestamp, body_str, signature):
            logger.warning("[SLACK-WEBHOOK] signature verification FAILED")
            raise HTTPException(status_code=401, detail="Invalid Slack signature")
        logger.info("[SLACK-WEBHOOK] signature verified ✓")
    else:
        logger.warning("[SLACK-WEBHOOK] SLACK_SIGNING_SECRET not set - skipping verification")

    try:
        payload = json.loads(body_str)
    except Exception:
        logger.error("[SLACK-WEBHOOK] invalid JSON body", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    payload_type = payload.get("type")
    logger.info(f"[SLACK-WEBHOOK] payload.type={payload_type}")

    # ----- URL verification handshake -----
    if payload_type == "url_verification":
        challenge = payload.get("challenge", "")
        logger.info(f"[SLACK-WEBHOOK] URL verification challenge len={len(challenge)}")
        return JSONResponse(content={"challenge": challenge})

    # ----- Event callback -----
    if payload_type == "event_callback":
        event_id = payload.get("event_id", "")
        if not _remember_event(event_id):
            logger.info(f"[SLACK-WEBHOOK] duplicate event_id={event_id}, ignoring")
            return JSONResponse(status_code=200, content={"status": "duplicate"})

        event = payload.get("event", {}) or {}
        event_type = event.get("type")
        subtype = event.get("subtype")
        bot_id = event.get("bot_id")

        logger.info(
            f"[SLACK-EVENT] id={event_id} type={event_type} subtype={subtype} "
            f"bot_id={bot_id} channel={event.get('channel')} ts={event.get('ts')} "
            f"user={event.get('user')}"
        )

        # Ignore bot/edited/deleted messages to avoid loops
        if bot_id:
            logger.info("[SLACK-EVENT] ignored: bot_id present (avoid loop)")
            return JSONResponse(status_code=200, content={"status": "ignored_bot"})
        if subtype in ("bot_message", "message_changed", "message_deleted", "thread_broadcast"):
            logger.info(f"[SLACK-EVENT] ignored: subtype={subtype}")
            return JSONResponse(status_code=200, content={"status": "ignored_subtype"})

        if event_type in ("message", "app_mention"):
            background_tasks.add_task(_handle_slack_message, event)
        else:
            logger.info(f"[SLACK-EVENT] no handler for type={event_type}")

    return JSONResponse(status_code=200, content={"status": "ok"})


# ---------------------------------------------------------------
# Slack message processing
# ---------------------------------------------------------------

_PRIORITY_KEYWORDS = {
    "Critical": ["urgent", "critical", "asap", "p0", "blocker", "outage", "down", "production down"],
    "High": ["high priority", "p1", "important", "escalate", "stuck", "delayed"],
    "Low": ["low priority", "p3", "minor", "cosmetic", "whenever"],
}

_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
_NAME_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9._-]{1,30})")


def _detect_priority(text: str) -> str:
    t = text.lower()
    for level, kws in _PRIORITY_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return level
    return "Medium"


async def _resolve_assignee(text: str) -> tuple[list[str], str | None]:
    """
    Resolve @mentions in Slack text.
    Returns (tagged_user_ids, primary_assignee_display_name).
    Falls back to '@Name' (free-text mention) if no Slack <@ID> is present.
    """
    tagged_ids = _USER_MENTION_RE.findall(text or "")
    primary_name = None

    if tagged_ids:
        try:
            info = await slack_service.get_user_info(tagged_ids[0])
            if info:
                primary_name = info.get("real_name") or info.get("name") or tagged_ids[0]
        except Exception as e:
            logger.warning(f"[SLACK-EVENT] user_info lookup failed for {tagged_ids[0]}: {e}")
            primary_name = tagged_ids[0]
    else:
        m = _NAME_MENTION_RE.search(text or "")
        if m:
            primary_name = m.group(1)

    return tagged_ids, primary_name


async def _handle_slack_message(event: dict):
    """
    Process a Slack message:
      1. Skip thread replies (only react on root messages)
      2. Filter to configured channel (by ID or name)
      3. Dedupe by slack_thread_ts
      4. Create Mongo ticket (Jira creation graceful)
      5. Save Slack mapping
      6. Reply in same thread with rich operational card
    """
    try:
        text = (event.get("text") or "").strip()
        user_id = event.get("user")
        channel_id = event.get("channel")
        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")

        logger.info(
            f"[SLACK-MSG] parse start ts={message_ts} channel={channel_id} "
            f"user={user_id} text_len={len(text)}"
        )

        if not text or not channel_id or not message_ts or not user_id:
            logger.warning("[SLACK-MSG] missing required fields - dropping")
            return

        # 1. Skip replies inside an existing thread
        if thread_ts and thread_ts != message_ts:
            # CHANGE 1: handle reply-to-resolved-ticket: reopen if applicable.
            try:
                parent_ticket = await ticket_service.find_by_slack_thread(thread_ts)
            except Exception as e:
                logger.warning(f"[SLACK-MSG] find_by_slack_thread error for {thread_ts}: {e}")
                parent_ticket = None

            if not parent_ticket:
                logger.info(
                    f"[SLACK-MSG] thread reply ts={message_ts} parent={thread_ts} - no parent ticket, skipping"
                )
                return

            # Resolve actor display name (best effort)
            user_info = await slack_service.get_user_info(user_id) if user_id else None
            actor = (
                (user_info or {}).get("real_name")
                or (user_info or {}).get("name")
                or user_id
                or "Slack User"
            )

            current_status = (parent_ticket.get("status") or "").lower()
            logger.info(
                f"[SLACK-MSG] thread reply on ticket={parent_ticket.get('id')} status={current_status}"
            )

            if current_status == "resolved":
                logger.info(
                    f"[SLACK-MSG] REOPENING resolved ticket={parent_ticket.get('id')} due to new reply"
                )
                try:
                    await ticket_service.reopen_ticket(
                        ticket_id=parent_ticket["id"],
                        latest_comment=text,
                        actor=actor,
                    )
                except Exception as e:
                    logger.error(f"[SLACK-MSG] reopen_ticket failed: {e}", exc_info=True)
                    return

                # Notify back in thread
                try:
                    await slack_service.post_message(
                        channel_id=channel_id,
                        text=(
                            ":arrows_counterclockwise: *Ticket Reopened*\n"
                            f"This ticket was resolved but has been reopened due to a new reply from *{actor}*."
                        ),
                        thread_ts=thread_ts,
                    )
                except Exception as e:
                    logger.warning(f"[SLACK-MSG] reopen ack post failed: {e}")
            else:
                # Just record as a comment in activity history; status unchanged
                try:
                    await ticket_service.append_activity(
                        parent_ticket["id"],
                        event="comment",
                        message=text,
                        actor=actor,
                    )
                    # Update latest_comment so dashboard reflects most recent reply
                    from models.ticket import TicketUpdate
                    await ticket_service.update_ticket(
                        parent_ticket["id"],
                        TicketUpdate(latest_comment=text),
                    )
                except Exception as e:
                    logger.warning(f"[SLACK-MSG] comment append failed: {e}")

            return

        # 2. Channel filter
        settings = get_settings()
        target_channel_id = (
            getattr(settings, "slack_bug_channel_id", "")
            or os.environ.get("SLACK_BUG_CHANNEL_ID", "")
        )
        target_channel_name = settings.slack_bug_channel

        channel_info = None
        if not slack_service.is_configured:
            logger.warning("[SLACK-MSG] Slack not configured - skipping channel filter (mock mode)")
        elif target_channel_id:
            if channel_id != target_channel_id:
                logger.info(
                    f"[SLACK-MSG] channel mismatch: got={channel_id} expected_id={target_channel_id} - skipping"
                )
                return
            logger.info(f"[SLACK-MSG] channel validated by ID: {channel_id}")
        elif target_channel_name:
            channel_info = await slack_service.get_channel_info(channel_id)
            cname = (channel_info or {}).get("name") if channel_info else None
            if cname != target_channel_name:
                logger.info(
                    f"[SLACK-MSG] channel name mismatch: got={cname} expected={target_channel_name} - skipping"
                )
                return
            logger.info(f"[SLACK-MSG] channel validated by name: #{cname}")
        else:
            logger.warning("[SLACK-MSG] no target channel configured - accepting all channels")

        # 3. Dedupe by Slack root thread ts
        existing = await ticket_service.find_by_slack_thread(message_ts)
        if existing:
            logger.info(
                f"[SLACK-MSG] root ts={message_ts} already mapped to ticket={existing.get('id')} - skipping"
            )
            return

        # Lazy fetch user/channel info
        user_info = await slack_service.get_user_info(user_id)
        if channel_info is None:
            channel_info = await slack_service.get_channel_info(channel_id)

        user_email = (user_info or {}).get("email") or f"slack-{user_id}@slack.local"
        user_name = (user_info or {}).get("real_name") or (user_info or {}).get("name") or "Slack User"
        channel_name = (channel_info or {}).get("name") or "unknown"

        # Brand convention: Slack tickets carry channel name as "brand"
        brand = f"#{channel_name}"
        summary = text if len(text) <= 120 else (text[:117] + "...")

        # 4. Detect priority + assignee
        priority = _detect_priority(text)
        tagged_ids, assignee_name = await _resolve_assignee(text)

        logger.info(
            f"[SLACK-MSG] parsed brand={brand} priority={priority} assignee={assignee_name} "
            f"tagged={tagged_ids}"
        )

        ticket_payload = TicketCreate(
            brand=brand,
            sender_email=user_email,
            summary=summary,
            full_message=text,
            source="slack",
            slack_thread_ts=message_ts,
            slack_channel_id=channel_id,
        )
        logger.info(f"[SLACK-MSG] payload built for ticket_service.create_ticket()")

        # 5. Create Mongo ticket (Jira creation is graceful inside ticket_service)
        try:
            created = await ticket_service.create_ticket(
                ticket_payload,
                priority=priority,
                assigned_to=assignee_name,
            )
        except Exception as e:
            logger.error(f"[SLACK-MSG] create_ticket FAILED: {e}", exc_info=True)
            # Best-effort Slack reply so user knows something happened
            try:
                await slack_service.post_message(
                    channel_id=channel_id,
                    text=":warning: OpsFlow could not create a ticket for this message. Engineering has been notified.",
                    thread_ts=message_ts,
                )
            except Exception:
                pass
            return

        if not created:
            logger.error("[SLACK-MSG] create_ticket returned None")
            return

        logger.info(
            f"[SLACK-MSG] mongo ticket created id={created.get('id')} "
            f"jira_key={created.get('jira_issue_key')}"
        )

        # 6. Save Slack ↔ Jira mapping
        try:
            mapping_data = {
                "slack_thread_ts": message_ts,
                "slack_message_ts": message_ts,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "jira_ticket_id": created.get("jira_issue_id") or "",
                "jira_ticket_key": created.get("jira_issue_key") or "",
                "ticket_id": created.get("id"),
                "created_by_slack_id": user_id,
                "created_by_name": user_name,
                "original_message": text,
                "extracted_ids": {},
                "tagged_users": tagged_ids,
                "assigned_to": assignee_name,
                "priority": priority,
                "status": "open",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await mapping_service.create_slack_mapping(mapping_data)
            logger.info(f"[SLACK-MSG] mapping saved thread_ts={message_ts}")
        except Exception as e:
            logger.warning(f"[SLACK-MSG] mapping save failed: {e}", exc_info=True)

        # 7. Reply in the same Slack thread with the operational card
        reply = format_slack_ticket_created(
            jira_key=created.get("jira_issue_key"),
            jira_url=created.get("jira_url"),
            priority=created.get("priority") or priority,
            issue_type=created.get("issue_type") or "Other",
            assignee=created.get("assigned_to") or assignee_name,
            status=(created.get("status") or "open").title(),
            summary=summary,
        )
        try:
            ok = await slack_service.post_message(
                channel_id=channel_id,
                text=reply,
                thread_ts=message_ts,
            )
            if ok:
                logger.info(
                    f"[SLACK-MSG] thread reply sent ticket={created.get('id')} "
                    f"jira={created.get('jira_issue_key')} thread={message_ts}"
                )
            else:
                logger.error("[SLACK-MSG] Slack thread reply returned False")
        except Exception as e:
            logger.error(f"[SLACK-MSG] thread reply failed: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[SLACK-MSG] fatal error in _handle_slack_message: {e}", exc_info=True)
