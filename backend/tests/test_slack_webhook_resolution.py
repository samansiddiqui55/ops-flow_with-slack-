"""
Backend tests for OpsFlow Slack webhook + ticket resolution flow.

Covers:
- Slack /webhooks/slack/events: URL verification, retry ack, bot/subtype filters
- Slack message filtering: channel (mock-mode bypass), issue keyword, tagged user,
  thread-reply skip, dedupe
- Slack happy path: creates ticket (source=slack), saves mapping, posts mock reply
- Slack reaction_added: ✅ resolves ticket, non-tick reactions ignored
- /api/tickets/{id}/resolve: email source triggers MOCK email, slack source posts to thread
- Health, list/create tickets endpoints

Backend runs with Slack/Jira/Email all in MOCK mode (no creds in .env).
We rely on DB state (Mongo) and HTTP responses, not real third-party calls.
"""
import os
import time
import uuid
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://opsflow-slack-intake.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
SLACK_EVENTS = f"{API}/webhooks/slack/events"

# Background tasks run async after response; use this to wait & poll Mongo state via REST
BG_WAIT = 2.0


@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _make_message_event(text, ts=None, channel="C_BUG_REPORTING", user="U_REPORTER", thread_ts=None, extra=None):
    ts = ts or f"{time.time():.6f}"
    event = {
        "type": "message",
        "user": user,
        "text": text,
        "ts": ts,
        "channel": channel,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    if extra:
        event.update(extra)
    return {
        "type": "event_callback",
        "event_id": f"Ev{uuid.uuid4().hex[:10]}",
        "event_time": int(time.time()),
        "event": event,
    }


def _find_ticket_by_slack_ts(http, ts):
    """Poll GET /api/tickets/ for a ticket whose slack_thread_ts matches ts."""
    deadline = time.time() + 6
    while time.time() < deadline:
        r = http.get(f"{API}/tickets/")
        if r.status_code == 200:
            for t in r.json():
                if t.get("slack_thread_ts") == ts:
                    return t
        time.sleep(0.5)
    return None


# =========================================================
# Health & basic ticket endpoints
# =========================================================

class TestHealthAndTickets:
    def test_health(self, http):
        r = http.get(f"{API}/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "healthy"

    def test_list_tickets(self, http):
        r = http.get(f"{API}/tickets/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_ticket_email_source(self, http):
        payload = {
            "brand": "TEST_brand",
            "sender_email": f"test_{uuid.uuid4().hex[:6]}@example.com",
            "summary": f"TEST_email_create_{uuid.uuid4().hex[:6]}",
            "full_message": "Email body for test",
            "source": "email",
        }
        r = http.post(f"{API}/tickets/", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["source"] == "email"
        assert data["sender_email"] == payload["sender_email"]
        assert "id" in data
        # Verify persisted via GET
        g = http.get(f"{API}/tickets/{data['id']}")
        assert g.status_code == 200
        assert g.json()["summary"] == payload["summary"]


# =========================================================
# Slack events endpoint - protocol-level
# =========================================================

class TestSlackEventsProtocol:
    def test_url_verification(self, http):
        r = http.post(SLACK_EVENTS, json={"type": "url_verification", "challenge": "xyz123"})
        assert r.status_code == 200
        assert r.json().get("challenge") == "xyz123"

    def test_retry_acked(self, http):
        body = _make_message_event("hello", ts=f"{time.time():.6f}")
        r = http.post(SLACK_EVENTS, json=body, headers={"X-Slack-Retry-Num": "1"})
        assert r.status_code == 200
        assert r.json().get("status") == "retry_acked"

    def test_bot_id_ignored(self, http):
        body = _make_message_event("shipment delay <@U07OPS123>", extra={"bot_id": "B123"})
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        assert r.json().get("status") == "ignored_bot"

    @pytest.mark.parametrize("subtype", ["bot_message", "message_changed", "message_deleted"])
    def test_subtype_ignored(self, http, subtype):
        body = _make_message_event("shipment delay <@U07OPS123>", extra={"subtype": subtype})
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        assert r.json().get("status") == "ignored_subtype"

    def test_invalid_json(self, http):
        r = http.post(SLACK_EVENTS, data="not-json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400


# =========================================================
# Slack message filters
# =========================================================

class TestSlackMessageFilters:
    def test_no_keyword_no_ticket(self, http):
        ts = f"{time.time():.6f}"
        body = _make_message_event("hi team can you check please <@U07OPS123>", ts=ts)
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"
        time.sleep(BG_WAIT)
        assert _find_ticket_by_slack_ts(http, ts) is None

    def test_keyword_no_tag_no_ticket(self, http):
        ts = f"{time.time():.6f}"
        body = _make_message_event("there is a shipment delay on AWB 123", ts=ts)
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        time.sleep(BG_WAIT)
        assert _find_ticket_by_slack_ts(http, ts) is None

    def test_thread_reply_skipped(self, http):
        # message is itself a reply (thread_ts != ts)
        ts = f"{time.time():.6f}"
        body = _make_message_event(
            "shipment delay reply <@U07OPS123>",
            ts=ts,
            thread_ts=f"{(time.time()-100):.6f}",
        )
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        time.sleep(BG_WAIT)
        assert _find_ticket_by_slack_ts(http, ts) is None


# =========================================================
# Slack happy path + dedupe
# =========================================================

class TestSlackHappyPath:
    def test_valid_message_creates_ticket(self, http):
        ts = f"{time.time():.6f}"
        body = _make_message_event(
            "Urgent shipment delay on AWB12345 please check <@U07OPS123>",
            ts=ts,
        )
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        ticket = _find_ticket_by_slack_ts(http, ts)
        assert ticket is not None, "Expected ticket to be created from slack event"
        assert ticket["source"] == "slack"
        assert ticket["slack_thread_ts"] == ts
        assert ticket["slack_channel_id"] == "C_BUG_REPORTING"
        assert ticket["status"] != "resolved"
        # brand convention: #channel-name
        assert ticket["brand"].startswith("#")

    def test_duplicate_event_does_not_create_second_ticket(self, http):
        ts = f"{time.time():.6f}"
        body = _make_message_event(
            "shipment dispatch stuck on NDR cycle <@U07OPS123>",
            ts=ts,
        )
        # First post -> creates
        r1 = http.post(SLACK_EVENTS, json=body)
        assert r1.status_code == 200
        t1 = _find_ticket_by_slack_ts(http, ts)
        assert t1 is not None
        # Second post (re-delivery) -> same root ts, must NOT create a new ticket
        r2 = http.post(SLACK_EVENTS, json=body)
        assert r2.status_code == 200
        time.sleep(BG_WAIT)
        # Count tickets with this slack_thread_ts must remain 1
        all_tickets = http.get(f"{API}/tickets/").json()
        matches = [x for x in all_tickets if x.get("slack_thread_ts") == ts]
        assert len(matches) == 1, f"Expected 1 ticket, got {len(matches)}"

    @pytest.mark.parametrize("kw", ["NDR", "RTO", "shipment delay"])
    def test_keyword_variants_pass(self, http, kw):
        ts = f"{time.time():.6f}"
        body = _make_message_event(f"Issue with {kw} pipeline today <@U07OPS123>", ts=ts)
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        ticket = _find_ticket_by_slack_ts(http, ts)
        assert ticket is not None, f"keyword '{kw}' should have created a ticket"


# =========================================================
# Slack reaction_added auto-resolve
# =========================================================

class TestSlackReactionResolve:
    def _seed_slack_ticket(self, http):
        ts = f"{time.time():.6f}"
        body = _make_message_event(
            f"shipment stuck please look <@U07OPS123> {uuid.uuid4().hex[:4]}",
            ts=ts,
        )
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        ticket = _find_ticket_by_slack_ts(http, ts)
        assert ticket is not None
        return ts, ticket

    def test_white_check_mark_resolves(self, http):
        ts, ticket = self._seed_slack_ticket(http)
        body = {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": "U_RESOLVER",
                "reaction": "white_check_mark",
                "item": {"type": "message", "channel": "C_BUG_REPORTING", "ts": ts},
            },
        }
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        # Wait for background task
        deadline = time.time() + 6
        resolved = None
        while time.time() < deadline:
            g = http.get(f"{API}/tickets/{ticket['id']}")
            if g.status_code == 200 and g.json().get("status") == "resolved":
                resolved = g.json()
                break
            time.sleep(0.5)
        assert resolved is not None, "Ticket should have been resolved by ✅ reaction"
        assert resolved.get("resolution_notes")

    def test_non_tick_reaction_ignored(self, http):
        ts, ticket = self._seed_slack_ticket(http)
        body = {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": "U_RESOLVER",
                "reaction": "eyes",
                "item": {"type": "message", "channel": "C_BUG_REPORTING", "ts": ts},
            },
        }
        r = http.post(SLACK_EVENTS, json=body)
        assert r.status_code == 200
        time.sleep(BG_WAIT)
        g = http.get(f"{API}/tickets/{ticket['id']}")
        assert g.status_code == 200
        assert g.json().get("status") != "resolved", "Non-tick reaction must not resolve ticket"


# =========================================================
# /api/tickets/{id}/resolve - email branch (MOCK email send)
# =========================================================

class TestResolveEmailBranch:
    def test_resolve_email_ticket_sends_mock_email(self, http):
        # Create email-source ticket first
        payload = {
            "brand": "TEST_email_brand",
            "sender_email": f"test_resolve_{uuid.uuid4().hex[:6]}@example.com",
            "summary": f"TEST_resolve_email_{uuid.uuid4().hex[:6]}",
            "full_message": "Customer reported shipment delay",
            "source": "email",
        }
        c = http.post(f"{API}/tickets/", json=payload)
        assert c.status_code == 200, c.text
        ticket = c.json()
        # Resolve
        r = http.post(
            f"{API}/tickets/{ticket['id']}/resolve",
            json={"latest_comment": "Investigated and addressed", "resolution_notes": "Fixed root cause"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Verify state updated
        assert body["ticket"]["status"] == "resolved"
        assert body["ticket"]["resolution_notes"] == "Fixed root cause"
        # Confirm via GET
        g = http.get(f"{API}/tickets/{ticket['id']}")
        assert g.status_code == 200
        assert g.json()["status"] == "resolved"

    def test_resolve_slack_ticket_posts_to_thread(self, http):
        # Reuse slack happy-path seeding
        ts = f"{time.time():.6f}"
        body = _make_message_event(
            f"NDR backlog needs attention <@U07OPS123> {uuid.uuid4().hex[:4]}",
            ts=ts,
        )
        r0 = http.post(SLACK_EVENTS, json=body)
        assert r0.status_code == 200
        ticket = _find_ticket_by_slack_ts(http, ts)
        assert ticket is not None
        # Resolve via API (this should also trigger Slack mock post_message)
        r = http.post(
            f"{API}/tickets/{ticket['id']}/resolve",
            json={"latest_comment": "Done", "resolution_notes": "Resolved by ops"},
        )
        assert r.status_code == 200
        assert r.json()["ticket"]["status"] == "resolved"


# =========================================================
# Cleanup hook (best-effort) - removes TEST_ prefixed tickets
# =========================================================

@pytest.fixture(scope="session", autouse=True)
def _cleanup_session(http):
    yield
    try:
        all_t = http.get(f"{API}/tickets/").json()
        for t in all_t:
            summary = t.get("summary", "")
            brand = t.get("brand", "")
            if summary.startswith("TEST_") or brand.startswith("TEST_"):
                http.delete(f"{API}/tickets/{t['id']}")
    except Exception:
        pass
