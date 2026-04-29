"""
Webhook routes - Slack events + Jira webhooks.

Slack flow (#bug-reporting only):
  Root message in #bug-reporting
    -> verify Slack signature
    -> ignore bot/edited/deleted/reply/retry events
    -> require shipment-issue keyword (aging, sync, shipment, delay, stuck,
       dispatch, delivery, ndr, rto)
    -> require at least one tagged user from the configured ops list
       (Siddiqui / Arushi / Deepak Singh by user ID, with display-name fallback)
    -> skip if root message already has a :white_check_mark: reaction
    -> skip if any thread reply already contains a resolution keyword
       (done / updated / resolved / fixed / completed / tick / closed)
    -> skip if the thread is already mapped to a Jira/dashboard ticket
    -> create dashboard ticket (source="slack") + Jira issue
       (reuses existing email->Jira logic via ticket_service.create_ticket)
    -> reply in same Slack thread with Jira link

Slack reaction flow:
  reaction_added of :white_check_mark: on a message that already has a Slack
  ticket -> auto-resolve the dashboard ticket (which triggers the existing
  Slack thread reply + Jira comment).

Jira flow (unchanged):
  Jira webhook on issue close
    -> if email mapping exists: send resolution email
    -> if slack mapping exists: post resolution in Slack thread
"""

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import json
import logging
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
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


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

        logger.info(f"Jira webhook received: {event_type} - {issue_key}")

        if not event_type or not issue_key:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        if event_type in ["jira:issue_updated", "issue_status_changed"]:
            changelog = payload.get("changelog", {})
            items = changelog.get("items", [])

            for item in items:
                if item.get("field") == "status":
                    new_status = item.get("toString", "")
                    if new_status.lower() in ["done", "closed", "resolved"]:
                        background_tasks.add_task(
                            _handle_jira_close,
                            issue_key,
                            issue.get("fields", {}).get("summary", ""),
                            new_status,
                        )

        return JSONResponse(status_code=200, content={"status": "received"})

    except Exception as e:
        logger.error(f"Jira webhook error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _handle_jira_close(issue_key: str, summary: str, status: str):
    """Send resolution to email or Slack thread when Jira issue closes."""
    try:
        logger.info(f"Processing Jira closure for {issue_key}")

        resolution_comment = (
            await jira_service.get_latest_comment(issue_key)
        ) or "Issue has been resolved."

        # Email resolution path
        email_mapping = await mapping_service.get_email_mapping_by_jira(issue_key)
        if email_mapping:
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
            logger.info(f"Resolution email sent for {issue_key}")

        # Slack resolution path
        slack_mapping = await mapping_service.get_slack_mapping_by_jira(issue_key)
        if slack_mapping:
            message = format_slack_resolution_message(
                issue_key, summary, resolution_comment
            )
            await slack_service.post_message(
                channel_id=slack_mapping.get("channel_id"),
                text=message,
                thread_ts=slack_mapping.get("slack_thread_ts"),
            )
            await mapping_service.update_slack_mapping_status(issue_key, "closed")
            logger.info(f"Resolution posted to Slack for {issue_key}")

    except Exception as e:
        logger.error(f"Error handling Jira close for {issue_key}: {str(e)}")


# =========================================================
# SLACK FILTER HELPERS
# =========================================================

# Slack tick reaction (✅) — used to mark a message as handled
RESOLVED_REACTIONS = {"white_check_mark", "heavy_check_mark", "white_tick", "tick"}


def _split_csv(value: str) -> list:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _has_issue_keyword(text: str, keywords: list) -> bool:
    """True if `text` contains any of `keywords` (case-insensitive, word boundary)."""
    if not keywords:
        return True  # filter disabled
    lowered = text.lower()
    for kw in keywords:
        kw = kw.strip().lower()
        if not kw:
            continue
        # word-boundary match for short tokens like ndr / rto, substring for longer phrases
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, lowered):
            return True
    return False


def _extract_mentioned_user_ids(text: str) -> list:
    """Return list of Slack user IDs mentioned via <@Uxxxx> syntax."""
    return re.findall(r"<@([A-Z0-9]+)>", text or "")


async def _has_required_tag(text: str) -> bool:
    """
    True if message tags any of the configured ops users.
    - First match by Slack user ID (preferred)
    - Fallback: resolve each mentioned ID via users.info and match real_name/name
      against SLACK_TAGGED_USER_NAMES (case-insensitive substring)
    - When neither IDs nor names are configured, this filter is OPEN (returns True)
      to allow flexible deployment.
    """
    settings = get_settings()
    required_ids = {uid.upper() for uid in _split_csv(settings.slack_tagged_user_ids)}
    required_names = [n.lower() for n in _split_csv(settings.slack_tagged_user_names)]

    if not required_ids and not required_names:
        return True  # filter disabled

    mentioned_ids = _extract_mentioned_user_ids(text)
    if not mentioned_ids:
        return False

    # 1) ID match
    if required_ids:
        for mid in mentioned_ids:
            if mid.upper() in required_ids:
                return True

    # 2) Name fallback (only if Slack is configured to look up users)
    if required_names and slack_service.is_configured:
        for mid in mentioned_ids:
            info = await slack_service.get_user_info(mid)
            if not info:
                continue
            haystacks = [
                (info.get("real_name") or "").lower(),
                (info.get("name") or "").lower(),
            ]
            for needle in required_names:
                if any(needle in h for h in haystacks if h):
                    return True

    return False


async def _is_already_handled(channel_id: str, thread_ts: str, root_text_lower: str) -> bool:
    """
    True if the thread is already considered handled by ops:
      - Root message has a ✅-style reaction
      - Any reply in the thread contains a resolution keyword
      - A Slack ticket mapping already exists for this thread
    """
    settings = get_settings()
    resolution_keywords = [k.lower() for k in _split_csv(settings.slack_resolution_keywords)]

    # 1. Existing mapping = already handled
    try:
        existing_map = await mapping_service.get_slack_mapping_by_thread(thread_ts)
        if existing_map:
            logger.info(f"Slack thread {thread_ts} already mapped to Jira, skipping")
            return True
    except Exception as e:
        logger.warning(f"Slack mapping lookup failed: {e}")

    # 2. Reactions on root message (✅ etc.)
    try:
        reactions = await slack_service.get_message_reactions(channel_id, thread_ts)
        for r in reactions:
            if (r.get("name") or "").lower() in RESOLVED_REACTIONS and (r.get("count") or 0) > 0:
                logger.info(f"Slack thread {thread_ts} has resolved reaction {r.get('name')}, skipping")
                return True
    except Exception as e:
        logger.warning(f"Slack reactions lookup failed: {e}")

    # 3. Replies containing resolution keyword
    try:
        replies = await slack_service.get_thread_replies(channel_id, thread_ts)
        # First message is the root itself; skip it
        for msg in replies[1:]:
            if msg.get("bot_id"):
                continue
            reply_text = (msg.get("text") or "").lower()
            if not reply_text:
                continue
            for kw in resolution_keywords:
                if not kw:
                    continue
                if re.search(r"\b" + re.escape(kw) + r"\b", reply_text):
                    logger.info(
                        f"Slack thread {thread_ts} reply already contains resolution keyword '{kw}', skipping"
                    )
                    return True
    except Exception as e:
        logger.warning(f"Slack thread replies lookup failed: {e}")

    return False


# =========================================================
# SLACK EVENTS
# =========================================================

@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """
    Slack Events API endpoint.
    Handles:
      - URL verification challenge
      - message.channels (root messages in bug channel)
      - app_mention
      - reaction_added (auto-resolve on ✅)

    Verifies Slack request signature when SLACK_SIGNING_SECRET is configured.
    Acks Slack retries quickly without reprocessing.
    """
    settings = get_settings()
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")

    # ----- Slack retry dedupe (ack-fast) -----
    retry_num = request.headers.get("X-Slack-Retry-Num")
    if retry_num and retry_num.isdigit() and int(retry_num) > 0:
        logger.info(f"Slack retry detected (attempt {retry_num}); acking without reprocessing")
        return JSONResponse(status_code=200, content={"status": "retry_acked"})

    # ----- Signature verification -----
    if settings.slack_signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not timestamp or not signature:
            logger.warning("Slack request missing signature headers")
            raise HTTPException(status_code=401, detail="Missing Slack signature headers")
        if not slack_service.verify_signature(timestamp, body_str, signature):
            logger.warning("Slack signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid Slack signature")
    else:
        logger.warning(
            "SLACK_SIGNING_SECRET not configured - skipping signature verification"
        )

    try:
        payload = json.loads(body_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # ----- URL verification handshake -----
    if payload.get("type") == "url_verification":
        return JSONResponse(content={"challenge": payload.get("challenge", "")})

    # ----- Event callback -----
    if payload.get("type") == "event_callback":
        event = payload.get("event", {}) or {}
        event_type = event.get("type")

        # Ignore bot/edited/deleted messages to avoid loops
        if event.get("bot_id"):
            return JSONResponse(status_code=200, content={"status": "ignored_bot"})
        if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return JSONResponse(status_code=200, content={"status": "ignored_subtype"})

        if event_type in ("message", "app_mention"):
            background_tasks.add_task(_handle_slack_message, event)
        elif event_type == "reaction_added":
            background_tasks.add_task(_handle_slack_reaction_added, event)

    return JSONResponse(status_code=200, content={"status": "ok"})


async def _handle_slack_reaction_added(event: dict):
    """
    When a ✅-style reaction is added to a Slack message that already has a
    dashboard ticket, auto-resolve the ticket (which sends the Slack thread
    reply + Jira comment via the existing resolve_ticket flow).
    """
    try:
        reaction_name = (event.get("reaction") or "").lower()
        if reaction_name not in RESOLVED_REACTIONS:
            return

        item = event.get("item") or {}
        if item.get("type") != "message":
            return
        message_ts = item.get("ts")
        if not message_ts:
            return

        ticket = await ticket_service.find_by_slack_thread(message_ts)
        if not ticket:
            logger.info(f"No ticket for slack ts={message_ts}; ignoring reaction")
            return
        if ticket.get("status") == "resolved":
            return

        await ticket_service.resolve_ticket(
            ticket_id=ticket["id"],
            latest_comment="Resolved via Slack ✅ reaction",
            resolution_notes="Marked done by ops team in Slack thread.",
        )
        logger.info(f"Auto-resolved ticket {ticket['id']} from Slack ✅ reaction")
    except Exception as e:
        logger.error(f"Error in _handle_slack_reaction_added: {str(e)}", exc_info=True)


async def _handle_slack_message(event: dict):
    """
    Process a Slack message in #bug-reporting:
      1. Skip thread replies (only root messages)
      2. Filter to configured channel (by ID or name)
      3. Require shipment-issue keyword
      4. Require an ops user tagged
      5. Skip if already handled (reaction / resolution-keyword reply / mapping)
      6. Dedupe by slack_thread_ts
      7. Create dashboard ticket (source=slack) + Jira issue
      8. Save Slack mapping
      9. Reply in same thread with Jira link
    """
    try:
        text = (event.get("text") or "").strip()
        user_id = event.get("user")
        channel_id = event.get("channel")
        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts")  # present if this message is itself a reply

        if not text or not channel_id or not message_ts or not user_id:
            logger.info("Slack event missing fields, ignoring")
            return

        # 1. Skip replies inside an existing thread (only handle root messages)
        if thread_ts and thread_ts != message_ts:
            logger.info(f"Skipping Slack thread reply ts={message_ts}")
            return

        settings = get_settings()
        target_channel_id = settings.slack_bug_channel_id
        target_channel_name = settings.slack_bug_channel

        # 2. Channel filter (skipped when Slack is in mock/unconfigured mode)
        channel_info = None
        if not slack_service.is_configured:
            logger.info("Slack in mock mode - skipping channel filter")
        elif target_channel_id:
            if channel_id != target_channel_id:
                logger.info(
                    f"Slack channel {channel_id} != configured ID {target_channel_id}, skipping"
                )
                return
        elif target_channel_name:
            channel_info = await slack_service.get_channel_info(channel_id)
            if not channel_info or channel_info.get("name") != target_channel_name:
                logger.info(
                    f"Slack channel name {channel_info.get('name') if channel_info else None} "
                    f"!= {target_channel_name}, skipping"
                )
                return

        # 3. Issue keyword filter
        keywords = _split_csv(settings.slack_issue_keywords)
        if not _has_issue_keyword(text, keywords):
            logger.info(f"Slack message ts={message_ts} has no issue keyword, skipping")
            return

        # 4. Tagged user filter
        if not await _has_required_tag(text):
            logger.info(
                f"Slack message ts={message_ts} does not tag a required ops user, skipping"
            )
            return

        # 5. Already-handled checks (reaction / resolved replies / existing mapping)
        if await _is_already_handled(channel_id, message_ts, text.lower()):
            return

        # 6. Dedupe by Slack root thread ts (dashboard side)
        existing = await ticket_service.find_by_slack_thread(message_ts)
        if existing:
            logger.info(
                f"Slack root message {message_ts} already has ticket {existing.get('id')}, skipping"
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

        ticket_payload = TicketCreate(
            brand=brand,
            sender_email=user_email,
            summary=summary,
            full_message=text,
            source="slack",
            slack_thread_ts=message_ts,
            slack_channel_id=channel_id,
        )

        # 7. Create ticket (also creates Jira issue inside ticket_service)
        created = await ticket_service.create_ticket(ticket_payload)
        if not created:
            logger.error("Slack ticket creation returned None")
            return

        # 8. Save legacy Slack mapping (used by Jira webhook path too)
        try:
            mapping_data = {
                "slack_thread_ts": message_ts,
                "slack_message_ts": message_ts,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "jira_ticket_id": created.get("jira_issue_id") or "",
                "jira_ticket_key": created.get("jira_issue_key") or "",
                "created_by_slack_id": user_id,
                "created_by_name": user_name,
                "original_message": text,
                "extracted_ids": {},
                "tagged_users": _extract_mentioned_user_ids(text),
                "status": "open",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await mapping_service.create_slack_mapping(mapping_data)
        except Exception as e:
            logger.warning(f"Slack mapping save failed: {e}")

        # 9. Reply in the same Slack thread
        jira_key = created.get("jira_issue_key")
        jira_url = created.get("jira_url")
        if jira_key:
            reply = (
                f":white_check_mark: Ticket created: *{jira_key}*"
                + (f"\n{jira_url}" if jira_url else "")
            )
        else:
            reply = ":white_check_mark: Ticket logged in OpsFlow"

        await slack_service.post_message(
            channel_id=channel_id,
            text=reply,
            thread_ts=message_ts,
        )

        logger.info(
            f"Slack ticket created id={created.get('id')} jira={jira_key} thread={message_ts}"
        )

    except Exception as e:
        logger.error(f"Error in _handle_slack_message: {str(e)}", exc_info=True)
