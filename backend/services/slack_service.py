from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import get_settings
import logging
import asyncio
from typing import Dict, Optional
import hmac
import hashlib
import time

logger = logging.getLogger(__name__)


class SlackService:
    """Service for Slack Bot API operations."""

    def __init__(self):
        settings = get_settings()
        self.bot_token = settings.slack_bot_token
        self.signing_secret = settings.slack_signing_secret
        self.bug_channel = settings.slack_bug_channel

        if self.bot_token:
            self.client = WebClient(token=self.bot_token)
            self.is_configured = True
        else:
            logger.warning("Slack not configured. Using mock mode.")
            self.client = None
            self.is_configured = False

    def verify_signature(self, timestamp: str, body: str, signature: str) -> bool:
        """Verify Slack request signature."""
        try:
            ts_int = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(time.time() - ts_int) > 60 * 5:
            return False

        sig_basestring = f"v0:{timestamp}:{body}".encode()
        computed_sig = "v0=" + hmac.new(
            self.signing_secret.encode(),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed_sig, signature)

    async def _run_blocking(self, fn, *args, **kwargs):
        """Run a blocking slack_sdk call in the default thread executor so it
        never blocks the FastAPI event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def post_message(self, channel_id: str, text: str, thread_ts: Optional[str] = None) -> bool:
        """Post a message to a Slack channel or thread."""
        if not self.is_configured:
            logger.info(f"[SLACK] MOCK post to {channel_id}: {text}")
            return True

        logger.info(
            f"[SLACK] posting reply channel={channel_id} thread_ts={thread_ts} len={len(text or '')}"
        )
        try:
            await self._run_blocking(
                self.client.chat_postMessage,
                channel=channel_id,
                text=text,
                thread_ts=thread_ts,
            )
            logger.info(f"[SLACK] reply success channel={channel_id} thread_ts={thread_ts}")
            return True
        except SlackApiError as e:
            logger.error(
                f"[SLACK] reply API error channel={channel_id} thread_ts={thread_ts}: {e}",
                exc_info=True,
            )
            return False
        except Exception as e:
            logger.error(
                f"[SLACK] reply unexpected error channel={channel_id} thread_ts={thread_ts}: {e}",
                exc_info=True,
            )
            return False

    async def get_user_info(self, user_id: str) -> Optional[Dict]:
        if not self.is_configured:
            return {"id": user_id, "name": "mock_user", "real_name": "Mock User"}
        try:
            response = await self._run_blocking(self.client.users_info, user=user_id)
            user = response["user"]
            return {
                "id": user["id"],
                "name": user["name"],
                "real_name": user.get("real_name", ""),
                "email": user.get("profile", {}).get("email", ""),
            }
        except SlackApiError as e:
            logger.error(f"[SLACK] users_info failed user={user_id}: {e}", exc_info=True)
            return None

    async def get_channel_info(self, channel_id: str) -> Optional[Dict]:
        if not self.is_configured:
            return {"id": channel_id, "name": "mock-channel"}
        try:
            response = await self._run_blocking(
                self.client.conversations_info, channel=channel_id
            )
            channel = response["channel"]
            return {
                "id": channel["id"],
                "name": channel["name"],
                "is_private": channel.get("is_private", False),
            }
        except SlackApiError as e:
            logger.error(
                f"[SLACK] conversations_info failed channel={channel_id}: {e}",
                exc_info=True,
            )
            return None

    async def get_permalink(self, channel_id: str, message_ts: str) -> Optional[str]:
        if not self.is_configured:
            return f"https://slack.com/mock/{channel_id}/{message_ts}"
        try:
            response = await self._run_blocking(
                self.client.chat_getPermalink,
                channel=channel_id,
                message_ts=message_ts,
            )
            return response["permalink"]
        except SlackApiError as e:
            logger.error(f"[SLACK] permalink failed: {e}", exc_info=True)
            return None


slack_service = SlackService()
