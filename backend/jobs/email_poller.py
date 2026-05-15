import asyncio
from datetime import datetime, timezone
import logging
import re

from services.email_service import email_service
from services.mapping_service import mapping_service
from services.ticket_service import ticket_service
from models.ticket import TicketCreate

logger = logging.getLogger(__name__)


class EmailPollerJob:
    """
    Email poller — runs ON THE FASTAPI MAIN EVENT LOOP via APScheduler's
    AsyncIOScheduler. This avoids the previous bug where a background thread
    with a separate event loop was binding Motor / Slack / ws_manager to a
    foreign loop, causing Slack thread replies (and other awaited calls) to
    silently hang in the FastAPI request handlers.

    The IMAP fetch (email_service.fetch_*) is synchronous; we offload it to a
    thread executor so it never blocks the loop.
    """

    def __init__(self):
        self._is_processing = False

    async def process_emails(self):
        """Poll and process incoming emails with UID tracking."""
        if self._is_processing:
            logger.info("[EMAIL-POLL] already in progress, skipping cycle")
            return

        self._is_processing = True
        try:
            logger.info("[EMAIL-POLL] starting cycle")

            # IMAP fetch is sync → offload to a thread so it doesn't block the loop
            loop = asyncio.get_running_loop()
            emails = await loop.run_in_executor(
                None, lambda: email_service.fetch_new_emails(50)
            )
            logger.info(f"[EMAIL-POLL] fetched {len(emails)} emails")

            processed = 0
            for email_data in emails:
                try:
                    await self.process_single_email(email_data)
                    processed += 1
                except Exception as e:
                    logger.error(f"[EMAIL-POLL] error processing email: {e}", exc_info=True)

            logger.info(f"[EMAIL-POLL] cycle done, processed={processed}")

        except Exception as e:
            logger.error(f"[EMAIL-POLL] fatal cycle error: {e}", exc_info=True)
        finally:
            self._is_processing = False

    async def process_single_email(self, email_data: dict):
        """Process a single email and create ticket if not duplicate."""
        message_id = (email_data.get("message_id") or "").strip()
        from_email = (email_data.get("from_email") or "").strip().lower()
        from_name = email_data.get("from_name")
        subject = (email_data.get("subject") or "No Subject").strip()
        body = (email_data.get("text") or "").strip()
        cc_emails = email_data.get("cc", [])

        if not from_email or not subject:
            logger.info("[EMAIL-POLL] skipping invalid email (missing sender/subject)")
            return

        logger.info(f"[EMAIL-POLL] processing from={from_email} subject={subject!r}")

        if message_id:
            existing_mapping = await mapping_service.get_email_mapping_by_thread(message_id)
            if existing_mapping:
                logger.info(f"[EMAIL-POLL] already processed message_id={message_id}")
                return

        brand = self.extract_brand_from_email(from_email)

        full_message = self.extract_clean_body(body)
        if not full_message.strip():
            html_content = email_data.get("html", "")
            if html_content:
                full_message = self.html_to_text(html_content)

        awb = self.extract_awb(subject + " " + body)

        ticket_payload = TicketCreate(
            brand=brand,
            sender_email=from_email,
            summary=subject,
            full_message=full_message,
            source="email",
            awb=awb,
        )

        created_ticket = await ticket_service.create_ticket(ticket_payload)

        if email_data.get("uid"):
            await email_service.mark_as_read(email_data["uid"])

        if message_id and created_ticket:
            mapping_data = {
                "email_thread_id": message_id,
                "message_id": message_id,
                "jira_ticket_id": created_ticket.get("jira_issue_id"),
                "jira_ticket_key": created_ticket.get("jira_issue_key"),
                "brand": brand,
                "sender_email": from_email,
                "sender_name": from_name,
                "original_subject": subject,
                "cc_emails": cc_emails,
                "status": "open",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await mapping_service.create_email_mapping(mapping_data)

        logger.info(
            f"[EMAIL-POLL] processed ticket={created_ticket.get('id')} "
            f"jira={created_ticket.get('jira_issue_key')}"
        )

    def extract_clean_body(self, text: str) -> str:
        if not text:
            return ""
        lines = text.split("\n")
        body_lines = []
        in_header = True
        for line in lines:
            if in_header:
                if line.lower().startswith((
                    "from:", "to:", "cc:", "bcc:", "subject:", "date:", "sent:",
                    "reply-to:", "x-", "content-type:", "mime-version:", "message-id:",
                )):
                    continue
                if line.strip() == "":
                    in_header = False
                    continue
                in_header = False
            body_lines.append(line)
        result = "\n".join(body_lines).strip()
        result = re.sub(r"--\s*\nSent from.*$", "", result, flags=re.MULTILINE | re.IGNORECASE)
        result = re.sub(r"\n*Get Outlook for.*$", "", result, flags=re.MULTILINE | re.IGNORECASE)
        return result.strip()

    def html_to_text(self, html: str) -> str:
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = (
            text.replace("&nbsp;", " ").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&")
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def extract_brand_from_email(self, sender_email: str) -> str:
        try:
            domain = sender_email.split("@")[-1].lower()
            ignored = {
                "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
                "icloud.com", "proton.me", "protonmail.com",
            }
            if domain in ignored:
                return "Unknown Brand"
            brand = domain.split(".")[0]
            return brand.replace("-", " ").replace("_", " ").title()
        except Exception:
            return "Unknown Brand"

    def extract_awb(self, text: str):
        patterns = [
            r"\bAWB[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\bawb[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\bTracking[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\btracking[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\bShipment[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\bshipment[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\bOrder[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\border[:\s\-#]*([A-Za-z0-9]{6,30})\b",
            r"\b(\d{10,20})\b",
            r"\b([A-Z]{2}\d{9}[A-Z]{2})\b",
            r"\b([A-Z]{3}\d{8,12})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def import_historical_emails(self):
        """Import ALL historical emails into dashboard (no Jira creation)."""
        logger.info("=" * 60)
        logger.info("STARTING HISTORICAL EMAIL IMPORT")
        logger.info("=" * 60)

        try:
            loop = asyncio.get_running_loop()
            emails = await loop.run_in_executor(
                None, lambda: email_service.fetch_all_emails(500)
            )
            logger.info(f"Found {len(emails)} historical emails to import")

            imported = 0
            skipped = 0

            for email_data in emails:
                try:
                    from_email = (email_data.get("from_email") or "").strip().lower()
                    subject = (email_data.get("subject") or "No Subject").strip()
                    body = (email_data.get("text") or "").strip()
                    if not from_email or not subject:
                        skipped += 1
                        continue
                    brand = self.extract_brand_from_email(from_email)
                    full_message = self.extract_clean_body(body)
                    if not full_message.strip():
                        html_content = email_data.get("html", "")
                        if html_content:
                            full_message = self.html_to_text(html_content)
                    awb = self.extract_awb(subject + " " + (body or ""))
                    email_date = email_data.get("date")
                    if email_date and not hasattr(email_date, "tzinfo"):
                        email_date = None

                    ticket_payload = TicketCreate(
                        brand=brand,
                        sender_email=from_email,
                        summary=subject,
                        full_message=full_message or f"Email from {from_email}: {subject}",
                        source="email",
                        awb=awb,
                    )
                    result = await ticket_service.create_display_ticket(
                        ticket_payload, email_date=email_date
                    )
                    if result:
                        imported += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.error(f"Error importing email: {e}", exc_info=True)
                    skipped += 1

            logger.info("=" * 60)
            logger.info(f"HISTORICAL IMPORT COMPLETE: {imported} imported, {skipped} skipped")
            logger.info("=" * 60)
            return {
                "status": "success",
                "total_fetched": len(emails),
                "imported": imported,
                "skipped": skipped,
            }
        except Exception as e:
            logger.error(f"Historical import error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    # ------------------------------------------------------------------
    # Lifecycle — server.py owns the AsyncIOScheduler. These wrappers exist
    # only so the rest of the codebase keeps the same API.
    # ------------------------------------------------------------------
    def start(self):
        """Deprecated: scheduling now happens in server.startup_event via
        AsyncIOScheduler bound to the FastAPI loop. This is a no-op kept for
        backwards compatibility with existing imports."""
        logger.info("[EMAIL-POLL] start() noop — AsyncIOScheduler in server.py owns scheduling")

    def stop(self):
        """Deprecated: scheduler shutdown handled in server.shutdown_event."""
        logger.info("[EMAIL-POLL] stop() noop — AsyncIOScheduler in server.py owns shutdown")


email_poller = EmailPollerJob()
