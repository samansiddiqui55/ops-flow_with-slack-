import re
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from models.ticket import TicketCreate, Ticket, TicketUpdate, classify_issue_type, classify_issue_type_hybrid
from config import get_settings
from motor.motor_asyncio import AsyncIOMotorClient
from services.jira_service import jira_service
import logging

# CHANGE 3: internal-clients exclusion for analytics
from filters.internal_clients import get_internal_match_filter, is_internal_brand
# CHANGE 6: clean display message for dashboard (raw full_message preserved in DB)
from utils.message_cleaner import build_display_message

logger = logging.getLogger(__name__)

settings = get_settings()
mongo_client = AsyncIOMotorClient(settings.mongo_url)
db = mongo_client[settings.db_name]
tickets_collection = db["tickets"]


def normalize_subject(subject: str) -> str:
    """Normalize subject for deduplication (remove RE:, FW:, etc.)"""
    subject = re.sub(r'^(re:|fw:|fwd:|aw:)\s*', '', subject.lower().strip(), flags=re.IGNORECASE)
    subject = re.sub(r'\s+', ' ', subject)
    return subject.strip()


def serialize_ticket(ticket: dict) -> dict:
    if not ticket:
        return ticket

    if "_id" in ticket:
        ticket["_id"] = str(ticket["_id"])

    for field in ["created_at", "updated_at", "resolved_at"]:
        if field in ticket and isinstance(ticket[field], datetime):
            ticket[field] = ticket[field].isoformat()

    # CHANGE 6: provide a cleaned, display-friendly message alongside the raw one.
    # Raw `full_message` stays untouched in DB; the UI prefers `display_message`.
    try:
        if "full_message" in ticket:
            ticket["display_message"] = build_display_message(ticket.get("full_message"))
    except Exception:
        ticket["display_message"] = ticket.get("full_message", "")

    return ticket


# CHANGE 3: Helper to apply internal-brand exclusion to analytics $match stages
def _apply_internal_filter(match_stage: dict) -> dict:
    """
    Merge the internal-clients exclusion ($and clause) into an existing match
    stage. Always returns a NEW dict (does not mutate input).
    """
    extra = get_internal_match_filter()
    if not extra:
        return dict(match_stage)
    merged = dict(match_stage)
    new_and = list(merged.get("$and", []))
    new_and.extend(extra["$and"])
    merged["$and"] = new_and
    return merged


# CHANGE 3 hotfix: ensure date-window filters work even when some legacy tickets
# stored `created_at` as ISO strings instead of BSON dates. We do TWO things:
#   1. If we have a date window, we OR-match both the date type AND the string
#      form (using $expr + $dateFromString) so no historical ticket is lost.
#   2. Outside of that, leave the match stage untouched.
def _wrap_created_at_for_dates(match_stage: dict) -> dict:
    """
    Replace any `created_at: {$gte/$lte: <datetime>}` filter with an $expr
    that uses $convert so legacy string ISO timestamps still match. Safe
    to call even if no created_at clause is present.
    """
    if "created_at" not in match_stage:
        return match_stage
    rng = match_stage.get("created_at")
    if not isinstance(rng, dict):
        return match_stage
    gte = rng.get("$gte")
    lte = rng.get("$lte")
    if gte is None and lte is None:
        return match_stage

    # $convert with onError=null + $ifNull fallback to avoid BSON conversion crash
    coerced = {
        "$convert": {
            "input": "$created_at",
            "to": "date",
            "onError": None,
            "onNull": None,
        }
    }

    expr_and = []
    if gte is not None:
        expr_and.append({"$gte": [coerced, gte]})
    if lte is not None:
        expr_and.append({"$lte": [coerced, lte]})

    new_stage = {k: v for k, v in match_stage.items() if k != "created_at"}
    new_stage["$expr"] = {"$and": expr_and} if len(expr_and) > 1 else expr_and[0]
    return new_stage


class TicketService:
    async def find_existing_open_ticket(self, sender_email: str, subject: str) -> Optional[dict]:
        """
        Prevent duplicate tickets:
        same sender + normalized subject + still unresolved = do not create again
        """
        try:
            normalized = normalize_subject(subject)
            
            # Check all open tickets from same sender
            cursor = tickets_collection.find({
                "sender_email": sender_email.lower().strip(),
                "status": {"$ne": "resolved"}
            })
            
            async for ticket in cursor:
                ticket_subject_normalized = normalize_subject(ticket.get("summary", ""))
                if ticket_subject_normalized == normalized:
                    logger.info(
                        f"Duplicate open ticket found for sender={sender_email}, subject={subject}"
                    )
                    return ticket

            return None

        except Exception as e:
            logger.error(f"Error checking duplicate ticket: {str(e)}")
            return None

    async def create_ticket(
        self,
        payload: TicketCreate,
        priority: str = "Medium",
        assigned_to: Optional[str] = None,
    ) -> dict:
        """
        Create ticket in Mongo + Jira.
        Includes issue_type classification, priority, and assignee.
        Jira failure does NOT block local ticket creation.
        """

        # 1. Check duplicate before creating
        existing_ticket = await self.find_existing_open_ticket(
            sender_email=payload.sender_email,
            subject=payload.summary
        )

        if existing_ticket:
            logger.info(
                f"Skipping duplicate ticket creation for {payload.sender_email} | {payload.summary}"
            )
            return serialize_ticket(existing_ticket)

        # 2. Classify issue type (CHANGE 5: hybrid keyword + optional LLM fallback)
        try:
            issue_type = await classify_issue_type_hybrid(payload.summary, payload.full_message)
        except Exception as e:
            logger.warning(f"[CLASSIFY] hybrid failed, using keyword: {e}")
            issue_type = classify_issue_type(payload.summary, payload.full_message)
        logger.info(f"Classified issue_type={issue_type} priority={priority} assignee={assigned_to}")

        jira_issue_key = None
        jira_issue_id = None
        jira_url = None

        # 3. Try Jira creation (graceful - never blocks local ticket)
        try:
            jira_result = await jira_service.create_issue(
                project_key=settings.jira_project_key,
                summary=payload.summary,
                description=f"[{issue_type}] [{priority}]\n\n{payload.full_message}",
                issue_type="Task",
                priority=priority if priority in ("Highest", "High", "Medium", "Low", "Lowest") else "Medium",
            )

            jira_issue_key = jira_result.get("issue_key")
            jira_issue_id = jira_result.get("issue_id")
            jira_url = jira_result.get("jira_url")

            logger.info(f"Jira issue linked: key={jira_issue_key} url={jira_url}")

        except Exception as e:
            logger.warning(
                f"Jira issue creation failed - continuing local-only. Error: {e}",
                exc_info=True,
            )

        # 4. Create local ticket
        ticket = Ticket(
            brand=payload.brand,
            sender_email=payload.sender_email.lower().strip(),
            summary=payload.summary,
            full_message=payload.full_message,
            source=payload.source,
            awb=payload.awb,
            issue_type=issue_type,
            priority=priority,
            assigned_to=assigned_to,
            jira_issue_key=jira_issue_key,
            jira_issue_id=jira_issue_id,
            jira_url=jira_url,
            slack_thread_ts=payload.slack_thread_ts,
            slack_channel_id=payload.slack_channel_id,
        )

        ticket_dict = ticket.model_dump()
        await tickets_collection.insert_one(ticket_dict)
        logger.info(f"Mongo ticket inserted id={ticket.id} source={payload.source}")

        saved_ticket = await tickets_collection.find_one({"id": ticket.id})
        serialized = serialize_ticket(saved_ticket)

        # Broadcast new ticket via WebSocket
        try:
            import builtins
            if hasattr(builtins, "ws_manager"):
                asyncio.ensure_future(builtins.ws_manager.broadcast({
                    "type": "new_ticket",
                    "ticket": serialized,
                }))
                logger.info(f"WebSocket broadcast queued for ticket={ticket.id}")
        except Exception as e:
            logger.warning(f"WebSocket broadcast failed: {e}", exc_info=True)

        return serialized

    async def get_all_tickets(self) -> List[dict]:
        tickets = []
        async for ticket in tickets_collection.find().sort("created_at", -1):
            tickets.append(serialize_ticket(ticket))
        return tickets

    async def create_display_ticket(self, payload: TicketCreate, email_date=None) -> Optional[dict]:
        """
        Create a display-only ticket (NO Jira creation).
        Used for historical email import - shows in dashboard but doesn't create Jira tickets.
        """
        # Check for duplicate
        existing_ticket = await self.find_existing_open_ticket(
            sender_email=payload.sender_email,
            subject=payload.summary
        )
        if existing_ticket:
            return None  # Skip duplicates silently

        # Classify issue type
        issue_type = classify_issue_type(payload.summary, payload.full_message)

        ticket = Ticket(
            brand=payload.brand,
            sender_email=payload.sender_email.lower().strip(),
            summary=payload.summary,
            full_message=payload.full_message,
            source=payload.source,
            awb=payload.awb,
            issue_type=issue_type,
            jira_issue_key=None,
            jira_issue_id=None,
            jira_url=None,
            status="open"
        )

        # Use email date if provided for historical accuracy
        if email_date:
            ticket.created_at = email_date
            ticket.updated_at = email_date

        ticket_dict = ticket.model_dump()
        await tickets_collection.insert_one(ticket_dict)
        
        return serialize_ticket(ticket_dict)

    async def get_ticket_by_id(self, ticket_id: str) -> Optional[dict]:
        ticket = await tickets_collection.find_one({"id": ticket_id})
        return serialize_ticket(ticket) if ticket else None

    async def update_ticket(self, ticket_id: str, payload: TicketUpdate) -> Optional[dict]:
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        update_data["updated_at"] = datetime.now(timezone.utc)

        await tickets_collection.update_one(
            {"id": ticket_id},
            {"$set": update_data}
        )

        updated_ticket = await tickets_collection.find_one({"id": ticket_id})
        return serialize_ticket(updated_ticket) if updated_ticket else None

    async def resolve_ticket(
        self,
        ticket_id: str,
        latest_comment: str = "",
        resolution_notes: str = ""
    ) -> Optional[dict]:
        ticket = await tickets_collection.find_one({"id": ticket_id})
        if not ticket:
            return None

        # Calculate TAT (turnaround time)
        resolved_at = datetime.now(timezone.utc)
        created_at = ticket.get("created_at")
        tat_hours = None
        
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            
            delta = resolved_at - created_at
            tat_hours = round(delta.total_seconds() / 3600, 2)  # Convert to hours

        await tickets_collection.update_one(
            {"id": ticket_id},
            {
                "$set": {
                    "status": "resolved",
                    "latest_comment": latest_comment,
                    "resolution_notes": resolution_notes,
                    "resolved_at": resolved_at,
                    "tat_hours": tat_hours,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )

        # CHANGE 1: append activity event so reopen flow has full history
        try:
            await self.append_activity(
                ticket_id, event="resolved",
                message=resolution_notes or latest_comment or "Ticket resolved",
                actor="OpsFlow Dashboard",
            )
        except Exception as e:
            logger.warning(f"[RESOLVE] append_activity failed: {e}")

        jira_issue_key = ticket.get("jira_issue_key")
        if jira_issue_key and latest_comment:
            try:
                await jira_service.add_comment(jira_issue_key, latest_comment)
            except Exception as e:
                logger.warning(f"Failed to add Jira comment for {jira_issue_key}: {str(e)}")

        # Post resolution back to Slack thread when ticket originated from Slack
        if ticket.get("source") == "slack":
            slack_thread_ts = ticket.get("slack_thread_ts")
            slack_channel_id = ticket.get("slack_channel_id")
            if slack_thread_ts and slack_channel_id:
                try:
                    from services.slack_service import slack_service
                    from utils.formatters import format_slack_resolution_message

                    resolution_text = (
                        resolution_notes
                        or latest_comment
                        or "Issue has been resolved."
                    )
                    message = format_slack_resolution_message(
                        ticket_key=jira_issue_key or ticket.get("id", "")[:8],
                        ticket_summary=ticket.get("summary", ""),
                        resolution_comment=resolution_text,
                        resolved_by="OpsFlow Dashboard",
                    )
                    await slack_service.post_message(
                        channel_id=slack_channel_id,
                        text=message,
                        thread_ts=slack_thread_ts,
                    )
                    logger.info(
                        f"Posted resolution to Slack thread {slack_thread_ts} in {slack_channel_id}"
                    )
                except Exception as e:
                    logger.warning(f"Slack thread reply failed: {e}")
            else:
                logger.warning(
                    f"Slack ticket {ticket.get('id')} missing thread_ts/channel_id; cannot reply"
                )

        updated_ticket = await tickets_collection.find_one({"id": ticket_id})
        serialized = serialize_ticket(updated_ticket) if updated_ticket else None
        
        # Broadcast ticket resolved via WebSocket
        if serialized:
            try:
                import builtins
                if hasattr(builtins, 'ws_manager'):
                    asyncio.ensure_future(builtins.ws_manager.broadcast({
                        "type": "ticket_resolved",
                        "ticket": serialized
                    }))
            except Exception as e:
                logger.warning(f"WebSocket broadcast failed: {e}")
        
        return serialized

    async def delete_ticket(self, ticket_id: str) -> bool:
        result = await tickets_collection.delete_one({"id": ticket_id})
        return result.deleted_count > 0

    # ------------------------------------------------------------------
    # CHANGE 1: Reopen on new Slack/email reply
    # ------------------------------------------------------------------
    async def append_activity(
        self,
        ticket_id: str,
        event: str,
        message: str = "",
        actor: str = "",
    ) -> None:
        """Append an entry to the ticket's activity_history. Safe / no-op on error."""
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,             # e.g. "resolved", "reopened", "comment"
                "message": (message or "")[:2000],
                "actor": actor or "",
            }
            await tickets_collection.update_one(
                {"id": ticket_id},
                {"$push": {"activity_history": entry}},
            )
            logger.info(f"[ACTIVITY] ticket={ticket_id} event={event} actor={actor!r}")
        except Exception as e:
            logger.warning(f"[ACTIVITY] failed for ticket={ticket_id}: {e}", exc_info=True)

    async def reopen_ticket(
        self,
        ticket_id: str,
        latest_comment: str = "",
        actor: str = "",
    ) -> Optional[dict]:
        """
        Reopen a resolved ticket because a new reply/comment arrived.
        - Sets status back to "open"
        - Stores latest_comment + actor
        - Clears resolved_at / tat_hours so resolution metrics reflect final close
        - Appends activity_history entries
        - Posts a Jira comment if linked
        - Broadcasts via WebSocket
        """
        ticket = await tickets_collection.find_one({"id": ticket_id})
        if not ticket:
            logger.warning(f"[REOPEN] ticket not found: {ticket_id}")
            return None

        prev_status = (ticket.get("status") or "").lower()
        if prev_status != "resolved":
            # Not resolved; just record the new comment as activity, but don't transition state.
            await self.append_activity(
                ticket_id, event="comment", message=latest_comment, actor=actor
            )
            await tickets_collection.update_one(
                {"id": ticket_id},
                {"$set": {"latest_comment": latest_comment or ticket.get("latest_comment"),
                          "updated_at": datetime.now(timezone.utc)}},
            )
            updated = await tickets_collection.find_one({"id": ticket_id})
            return serialize_ticket(updated) if updated else None

        # Build update
        now = datetime.now(timezone.utc)
        reopen_count = int(ticket.get("reopen_count") or 0) + 1
        await tickets_collection.update_one(
            {"id": ticket_id},
            {
                "$set": {
                    "status": "open",
                    "latest_comment": latest_comment or "Reopened due to new reply",
                    "resolved_at": None,
                    "tat_hours": None,
                    "reopen_count": reopen_count,
                    "updated_at": now,
                }
            },
        )

        # Activity entries: reopened + new comment
        await self.append_activity(
            ticket_id,
            event="reopened",
            message=f"Ticket reopened due to new reply (was resolved)",
            actor=actor,
        )
        if latest_comment:
            await self.append_activity(
                ticket_id, event="comment", message=latest_comment, actor=actor
            )

        # Jira: add comment + (best-effort) transition back to open
        jira_issue_key = ticket.get("jira_issue_key")
        if jira_issue_key:
            try:
                comment = f"Reopened by OpsFlow — new reply received.\n\n{latest_comment}"
                await jira_service.add_comment(jira_issue_key, comment)
                logger.info(f"[REOPEN] jira comment added for {jira_issue_key}")
            except Exception as e:
                logger.warning(f"[REOPEN] jira comment failed for {jira_issue_key}: {e}")

        updated = await tickets_collection.find_one({"id": ticket_id})
        serialized = serialize_ticket(updated) if updated else None

        # Broadcast
        if serialized:
            try:
                import builtins
                if hasattr(builtins, "ws_manager"):
                    asyncio.ensure_future(builtins.ws_manager.broadcast({
                        "type": "ticket_reopened",
                        "ticket": serialized,
                    }))
                    logger.info(f"[REOPEN] WS broadcast ticket={ticket_id}")
            except Exception as e:
                logger.warning(f"[REOPEN] WS broadcast failed: {e}")

        return serialized

    async def find_by_slack_thread(self, slack_thread_ts: str) -> Optional[dict]:
        """Find an existing ticket created from a Slack root message (used to dedupe)."""
        if not slack_thread_ts:
            return None
        ticket = await tickets_collection.find_one({"slack_thread_ts": slack_thread_ts})
        return serialize_ticket(ticket) if ticket else None

    async def resolve_ticket_by_jira_key(
        self,
        jira_key: str,
        latest_comment: str = "",
        resolution_notes: str = "",
        resolved_by: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Mark a ticket resolved by Jira issue key (used by Jira webhook).
        Skips Slack post (caller already handles that) but DOES broadcast over WebSocket.
        """
        ticket = await tickets_collection.find_one({"jira_issue_key": jira_key})
        if not ticket:
            logger.warning(f"resolve_ticket_by_jira_key: no ticket for {jira_key}")
            return None

        resolved_at = datetime.now(timezone.utc)
        created_at = ticket.get("created_at")
        tat_hours = None
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            tat_hours = round((resolved_at - created_at).total_seconds() / 3600, 2)

        await tickets_collection.update_one(
            {"id": ticket["id"]},
            {
                "$set": {
                    "status": "resolved",
                    "latest_comment": latest_comment,
                    "resolution_notes": resolution_notes,
                    "resolved_by": resolved_by,
                    "resolved_at": resolved_at,
                    "tat_hours": tat_hours,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        # CHANGE 1: append activity entry on Jira-driven resolve
        try:
            await self.append_activity(
                ticket["id"], event="resolved",
                message=resolution_notes or latest_comment or "Resolved via Jira",
                actor=resolved_by or "Jira",
            )
        except Exception as e:
            logger.warning(f"[RESOLVE-JIRA] append_activity failed: {e}")

        updated = await tickets_collection.find_one({"id": ticket["id"]})
        serialized = serialize_ticket(updated) if updated else None

        if serialized:
            try:
                import builtins
                if hasattr(builtins, "ws_manager"):
                    asyncio.ensure_future(builtins.ws_manager.broadcast({
                        "type": "ticket_resolved",
                        "ticket": serialized,
                    }))
                    logger.info(f"WS broadcast: ticket_resolved id={serialized.get('id')}")
            except Exception as e:
                logger.warning(f"WS broadcast failed: {e}")

        return serialized

    async def get_brand_frequency(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        source: Optional[str] = "email",
    ) -> List[dict]:
        """Brand histogram, optionally filtered by source (default: email tickets only). Excludes internal brands."""
        match_stage: dict = {}
        if source:
            match_stage["source"] = source
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3: exclude internal/test brands
        match_stage = _apply_internal_filter(match_stage)
        # CHANGE 3 hotfix: also handle legacy string created_at
        match_stage = _wrap_created_at_for_dates(match_stage)

        pipeline = []
        if match_stage:
            pipeline.append({"$match": match_stage})
        pipeline.extend([
            {"$group": {"_id": "$brand", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ])

        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({"brand": doc["_id"] or "Unknown", "count": doc["count"]})
        return results

    async def get_source_frequency(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[dict]:
        """Email vs Slack ticket counts. Excludes internal brands."""
        match_stage: dict = {}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3: exclude internal/test brands
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)

        pipeline = []
        if match_stage:
            pipeline.append({"$match": match_stage})
        pipeline.extend([
            {"$group": {"_id": "$source", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ])

        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({"source": doc["_id"] or "unknown", "count": doc["count"]})
        return results
    
    # Analytics methods
    async def get_issues_by_client(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[dict]:
        """Aggregate issues by client/brand. Excludes internal brands."""
        pipeline = []
        
        match_stage = {}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3: exclude internal/test brands
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)
        
        if match_stage:
            pipeline.append({"$match": match_stage})
        
        pipeline.extend([
            {
                "$group": {
                    "_id": {
                        "brand": "$brand",
                        "issue_type": "$issue_type"
                    },
                    "count": {"$sum": 1}
                }
            },
            {
                "$group": {
                    "_id": "$_id.brand",
                    "total": {"$sum": "$count"},
                    "by_type": {
                        "$push": {
                            "issue_type": "$_id.issue_type",
                            "count": "$count"
                        }
                    }
                }
            },
            {"$sort": {"total": -1}}
        ])
        
        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({
                "brand": doc["_id"] or "Unknown",
                "total": doc["total"],
                "by_type": doc["by_type"]
            })
        return results
    
    async def get_issue_type_distribution(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[dict]:
        """Aggregate issues by type. Excludes internal brands."""
        pipeline = []
        
        match_stage = {}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)
        
        if match_stage:
            pipeline.append({"$match": match_stage})
        
        pipeline.extend([
            {
                "$group": {
                    "_id": "$issue_type",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ])
        
        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({
                "issue_type": doc["_id"] or "Other",
                "count": doc["count"]
            })
        return results
    
    async def get_time_series(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[dict]:
        """Aggregate issues by date. Excludes internal brands."""
        pipeline = []
        
        match_stage = {}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)
        
        if match_stage:
            pipeline.append({"$match": match_stage})
        
        # CHANGE 3 hotfix: coerce created_at to date BEFORE $dateToString to handle
        # legacy tickets that may have stored created_at as ISO string.
        pipeline.append({
            "$addFields": {
                "_created_at_date": {
                    "$convert": {
                        "input": "$created_at",
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }
        })

        pipeline.extend([
            {"$match": {"_created_at_date": {"$ne": None}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$_created_at_date"
                        }
                    },
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
        ])
        
        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({
                "date": doc["_id"],
                "count": doc["count"]
            })
        return results
    
    async def get_tat_by_client(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[dict]:
        """Get average TAT (turnaround time) per client. Excludes internal brands."""
        pipeline = []
        
        match_stage = {"status": "resolved", "tat_hours": {"$ne": None}}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)
        
        pipeline.append({"$match": match_stage})
        
        pipeline.extend([
            {
                "$group": {
                    "_id": "$brand",
                    "avg_tat_hours": {"$avg": "$tat_hours"},
                    "min_tat_hours": {"$min": "$tat_hours"},
                    "max_tat_hours": {"$max": "$tat_hours"},
                    "resolved_count": {"$sum": 1}
                }
            },
            {"$sort": {"avg_tat_hours": 1}}
        ])
        
        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({
                "brand": doc["_id"] or "Unknown",
                "avg_tat_hours": round(doc["avg_tat_hours"], 2) if doc["avg_tat_hours"] else 0,
                "min_tat_hours": round(doc["min_tat_hours"], 2) if doc["min_tat_hours"] else 0,
                "max_tat_hours": round(doc["max_tat_hours"], 2) if doc["max_tat_hours"] else 0,
                "resolved_count": doc["resolved_count"]
            })
        return results
    
    async def get_tat_by_issue_type(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[dict]:
        """Get average TAT per issue type. Excludes internal brands."""
        pipeline = []
        
        match_stage = {"status": "resolved", "tat_hours": {"$ne": None}}
        if start_date or end_date:
            match_stage["created_at"] = {}
            if start_date:
                match_stage["created_at"]["$gte"] = start_date
            if end_date:
                match_stage["created_at"]["$lte"] = end_date

        # CHANGE 3
        match_stage = _apply_internal_filter(match_stage)
        match_stage = _wrap_created_at_for_dates(match_stage)
        
        pipeline.append({"$match": match_stage})
        
        pipeline.extend([
            {
                "$group": {
                    "_id": "$issue_type",
                    "avg_tat_hours": {"$avg": "$tat_hours"},
                    "min_tat_hours": {"$min": "$tat_hours"},
                    "max_tat_hours": {"$max": "$tat_hours"},
                    "resolved_count": {"$sum": 1}
                }
            },
            {"$sort": {"avg_tat_hours": 1}}
        ])
        
        results = []
        async for doc in tickets_collection.aggregate(pipeline):
            results.append({
                "issue_type": doc["_id"] or "Other",
                "avg_tat_hours": round(doc["avg_tat_hours"], 2) if doc["avg_tat_hours"] else 0,
                "min_tat_hours": round(doc["min_tat_hours"], 2) if doc["min_tat_hours"] else 0,
                "max_tat_hours": round(doc["max_tat_hours"], 2) if doc["max_tat_hours"] else 0,
                "resolved_count": doc["resolved_count"]
            })
        return results


ticket_service = TicketService()
