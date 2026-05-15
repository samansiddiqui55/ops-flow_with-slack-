"""
Backend regression tests for OpsFlow Changes 1-6.
Covers: reopen/resolve activity, email filters, internal client analytics
exclusion, time-period filters, issue type classification, display_message.
"""
import os
import sys
import time
import asyncio
import pytest
import requests
import websockets
import json
from datetime import datetime, timezone, timedelta

# Make backend/ importable so we can unit-test pure helpers
sys.path.insert(0, "/app/backend")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"


# ---------------- fixtures ----------------
@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def created_ticket(client):
    payload = {
        "brand": "TEST_BrandReopen",
        "sender_email": "test_reopen@example.com",
        "summary": "TEST_REOPEN: pincode not serviceable for 110001",
        "full_message": "From: test_reopen@example.com\nTo: support@x.com\nSubject: pincode\n\nHi team, the pincode 110001 is showing not serviceable in our panel.\n\nThanks,\nQA\nmailto:test_reopen@example.com",
        "source": "email",
    }
    r = client.post(f"{API}/tickets/", json=payload, timeout=15)
    assert r.status_code in (200, 201), r.text
    data = r.json()
    yield data
    # cleanup attempt
    try:
        client.delete(f"{API}/tickets/{data['id']}", timeout=10)
    except Exception:
        pass


# ---------------- General/Health ----------------
class TestHealth:
    def test_health(self, client):
        r = client.get(f"{API}/health", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") == "healthy"
        assert d.get("database") == "connected"

    def test_get_tickets_list(self, client):
        r = client.get(f"{API}/tickets/", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list)
        assert len(d) > 0


# ---------------- CHANGE 1: Resolve / Reopen ----------------
class TestResolveReopen:
    def test_resolve_sets_status_and_tat(self, client, created_ticket):
        tid = created_ticket["id"]
        r = client.post(
            f"{API}/tickets/{tid}/resolve",
            json={"latest_comment": "fixed", "resolution_notes": "root cause: bad mapping"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        t = r.json().get("ticket") or r.json()
        assert t["status"] == "resolved"
        assert t.get("tat_hours") is not None
        # Activity entry for resolved
        events = [e.get("event") for e in (t.get("activity_history") or [])]
        assert "resolved" in events

    def test_reopen_resolved_ticket(self, client, created_ticket):
        tid = created_ticket["id"]
        r = client.post(
            f"{API}/tickets/{tid}/reopen",
            json={"latest_comment": "Customer replied again"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        t = r.json().get("ticket") or r.json()
        assert t["status"] == "open"
        assert t.get("reopen_count", 0) >= 1
        assert t.get("resolved_at") in (None, "")
        assert t.get("tat_hours") in (None, 0)
        events = [e.get("event") for e in (t.get("activity_history") or [])]
        assert "reopened" in events
        assert "comment" in events

    def test_reopen_non_resolved_only_adds_comment(self, client):
        # Create a fresh open ticket
        payload = {
            "brand": "TEST_BrandOpenOnly",
            "sender_email": "openonly@example.com",
            "summary": "TEST_OPEN: webhook callback url 500 errors",
            "full_message": "webhook not triggered for orders",
            "source": "email",
        }
        r = client.post(f"{API}/tickets/", json=payload, timeout=15)
        assert r.status_code in (200, 201)
        tid = r.json()["id"]
        try:
            rr = client.post(
                f"{API}/tickets/{tid}/reopen",
                json={"latest_comment": "another reply"},
                timeout=15,
            )
            assert rr.status_code == 200
            t = rr.json().get("ticket") or rr.json()
            assert t["status"] == "open"
            # reopen_count must not increment because not resolved
            assert int(t.get("reopen_count") or 0) == 0
            events = [e.get("event") for e in (t.get("activity_history") or [])]
            assert "comment" in events
            assert "reopened" not in events
        finally:
            try:
                client.delete(f"{API}/tickets/{tid}", timeout=10)
            except Exception:
                pass


# ---------------- CHANGE 2: Email filters ----------------
class TestEmailFilters:
    def test_filter_module(self):
        from filters.email_filters import should_process_email

        # Blocked domains
        ok, reason = should_process_email("recruiter@naukri.com", "Job alert")
        assert ok is False
        ok, reason = should_process_email("sales@magicbricks.com", "Property")
        assert ok is False
        ok, _ = should_process_email("notif@nptel.ac.in", "Course")
        assert ok is False

        # Blocked sender patterns
        ok, _ = should_process_email("hr@somecorp.com", "Hello")
        assert ok is False
        ok, _ = should_process_email("noreply@bank.com", "OTP")
        assert ok is False
        ok, _ = should_process_email("jobs@anycorp.com", "Hiring")
        assert ok is False

        # Allowlist wins
        ok, reason = should_process_email("samansiddiqui903@gmail.com", "anything")
        assert ok is True
        assert reason == "allowlisted"
        ok, reason = should_process_email("ops@grow-simplee.com", "anything")
        assert ok is True

        # Normal client email allowed
        ok, _ = should_process_email("ops@mokobara.com", "Pincode issue")
        assert ok is True


# ---------------- CHANGE 3: Analytics exclude internal brands ----------------
INTERNAL_TERMS = {"blitznow", "#bug-reporting", "testing channel", "testing"}


def _has_internal(items, key):
    for it in items:
        v = (it.get(key) or "").strip().lower()
        if any(t in v for t in INTERNAL_TERMS):
            return v
    return None


class TestAnalyticsExclusion:
    def test_summary_excludes_internal(self, client):
        r = client.get(f"{API}/analytics/summary", timeout=15)
        assert r.status_code == 200
        d = r.json()
        ibc = d.get("issues_by_client") or []
        tat = d.get("tat_by_client") or []
        assert _has_internal(ibc, "brand") is None, f"Found internal in issues_by_client"
        assert _has_internal(tat, "brand") is None

    def test_brand_freq_excludes_internal(self, client):
        r = client.get(f"{API}/analytics/brand-frequency?source=all", timeout=15)
        assert r.status_code == 200
        data = r.json().get("data") or []
        assert _has_internal(data, "brand") is None

    def test_issues_by_client_excludes_internal(self, client):
        r = client.get(f"{API}/analytics/issues-by-client", timeout=15)
        assert r.status_code == 200
        data = r.json().get("data") or []
        assert _has_internal(data, "brand") is None

    def test_tat_by_client_excludes_internal(self, client):
        r = client.get(f"{API}/analytics/tat-by-client", timeout=15)
        assert r.status_code == 200
        data = r.json().get("data") or []
        assert _has_internal(data, "brand") is None

    def test_period_filters(self, client):
        all_r = client.get(f"{API}/analytics/summary", timeout=15).json()
        total_all = all_r["summary"]["total_issues"]
        r1w = client.get(f"{API}/analytics/summary?period=1w", timeout=15).json()
        r1m = client.get(f"{API}/analytics/summary?period=1m", timeout=15).json()
        r1y = client.get(f"{API}/analytics/summary?period=1y", timeout=15).json()
        t1w = r1w["summary"]["total_issues"]
        t1m = r1m["summary"]["total_issues"]
        t1y = r1y["summary"]["total_issues"]
        # Each shorter window must be <= longer window <= all
        assert t1w <= t1m, f"1w={t1w} > 1m={t1m}"
        assert t1m <= t1y, f"1m={t1m} > 1y={t1y}"
        assert t1y <= total_all, f"1y={t1y} > all={total_all}"


# ---------------- CHANGE 5: Issue type classification ----------------
class TestIssueClassification:
    def test_classifier(self):
        from models.ticket import classify_issue_type

        assert classify_issue_type(
            "Order delayed for 5 days",
            "shipment stuck in transit, delivery delay",
        ) == "Delay / TAT Issue"

        # Plain 'pending' should NOT be over-classified as Delay/TAT
        assert classify_issue_type("Pending", "order is pending") == "Other"

        assert classify_issue_type(
            "Webhook not triggered",
            "callback url 500",
        ) == "Webhook Issue"


# ---------------- CHANGE 6: display_message strips headers etc. ----------------
class TestDisplayMessage:
    def test_display_message_present_and_clean(self, client):
        r = client.get(f"{API}/tickets/", timeout=15)
        assert r.status_code == 200
        tickets = r.json()
        assert len(tickets) > 0
        sample = tickets[0]
        # display_message field must exist
        assert "display_message" in sample
        assert "full_message" in sample  # raw still preserved
        dm = (sample.get("display_message") or "").lower()
        # Should not have raw header prefixes at start
        for hdr in ("from:", "to:", "subject:", "mailto:"):
            assert hdr not in dm[:200], f"display_message still contains '{hdr}' near top: {dm[:200]}"

    def test_display_message_unit(self):
        from utils.message_cleaner import build_display_message

        raw = (
            "From: foo@bar.com\nTo: x@y.com\nSubject: Test\n\n"
            "Hello team, please check the issue.\n\n"
            "> previous quoted text\nOn Mon, X wrote:\n> reply line\n\n"
            "Thanks,\nFoo\nmailto:foo@bar.com"
        )
        out = build_display_message(raw)
        out_l = out.lower()
        assert "from:" not in out_l
        assert "to:" not in out_l
        assert "subject:" not in out_l
        assert "mailto:" not in out_l
        assert "Hello team" in out


# ---------------- WebSocket ----------------
class TestWebSocket:
    def test_ws_connect_and_broadcast(self, client):
        ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/ws/tickets"

        async def run():
            received = []
            async with websockets.connect(ws_url, open_timeout=10) as ws:
                # Trigger broadcast by creating a ticket
                async def listen():
                    try:
                        while True:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            received.append(json.loads(msg))
                    except Exception:
                        return

                listener = asyncio.create_task(listen())
                await asyncio.sleep(0.5)

                # Trigger new_ticket then resolve + reopen
                payload = {
                    "brand": "TEST_BrandWS",
                    "sender_email": "ws_test@example.com",
                    "summary": "TEST_WS: webhook not triggered",
                    "full_message": "callback url 500 errors",
                    "source": "email",
                }
                cr = client.post(f"{API}/tickets/", json=payload, timeout=15)
                tid = cr.json()["id"]
                client.post(
                    f"{API}/tickets/{tid}/resolve",
                    json={"latest_comment": "fixed", "resolution_notes": "done"},
                    timeout=15,
                )
                client.post(
                    f"{API}/tickets/{tid}/reopen",
                    json={"latest_comment": "regression"},
                    timeout=15,
                )
                await asyncio.sleep(3)
                listener.cancel()
                try:
                    await listener
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    client.delete(f"{API}/tickets/{tid}", timeout=10)
                except Exception:
                    pass
            return received

        events = asyncio.get_event_loop().run_until_complete(run()) if sys.version_info < (3, 10) else asyncio.run(run())
        types = {e.get("type") for e in events}
        assert "ticket_resolved" in types, f"types={types}"
        assert "ticket_reopened" in types, f"types={types}"
