"""Tests for Slack webhook flow + new analytics endpoints (brand/source frequency)."""
import os
import time
import json
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ticket-hub-221.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

TEST_SLACK_TS = []  # track created slack ts for cleanup if needed


def _slack_event(ts: str, text: str, channel="C999TEST", user="U999TEST", thread_ts=None):
    evt = {"type": "message", "channel": channel, "user": user, "text": text, "ts": ts}
    if thread_ts:
        evt["thread_ts"] = thread_ts
    return {"type": "event_callback", "event": evt, "event_id": f"Ev_{ts}"}


# ---------- Slack URL verification ----------
def test_slack_url_verification():
    r = requests.post(f"{API}/webhooks/slack/events",
                      json={"type": "url_verification", "challenge": "abc123xyz"},
                      timeout=10)
    assert r.status_code == 200, r.text
    assert r.json().get("challenge") == "abc123xyz"


# ---------- Slack root message creates ticket ----------
def test_slack_root_message_creates_ticket():
    ts = f"{int(time.time())}.000001"
    TEST_SLACK_TS.append(ts)
    payload = _slack_event(ts, "TEST_SLACK: Pincode 560001 not serviceable for brand Acme")
    r = requests.post(f"{API}/webhooks/slack/events", json=payload, timeout=10)
    assert r.status_code == 200, r.text
    # background task - wait for processing
    time.sleep(3)
    tickets = requests.get(f"{API}/tickets/", timeout=10).json()
    assert isinstance(tickets, list)
    match = [t for t in tickets if t.get("slack_thread_ts") == ts]
    assert len(match) == 1, f"Expected 1 slack ticket for ts={ts}, got {len(match)}"
    t = match[0]
    assert t["source"] == "slack"
    assert t["slack_channel_id"] == "C999TEST"
    assert t["slack_thread_ts"] == ts


# ---------- Dedupe on repeat ----------
def test_slack_dedupe_same_ts():
    ts = f"{int(time.time())}.000002"
    TEST_SLACK_TS.append(ts)
    p = _slack_event(ts, "TEST_SLACK: duplicate root message order creation failure")
    r1 = requests.post(f"{API}/webhooks/slack/events", json=p, timeout=10)
    assert r1.status_code == 200
    time.sleep(2)
    r2 = requests.post(f"{API}/webhooks/slack/events", json=p, timeout=10)
    assert r2.status_code == 200
    time.sleep(2)
    tickets = requests.get(f"{API}/tickets/", timeout=10).json()
    match = [t for t in tickets if t.get("slack_thread_ts") == ts]
    assert len(match) == 1, f"Dedupe failed: got {len(match)} tickets"


# ---------- Thread reply ignored ----------
def test_slack_thread_reply_ignored():
    root_ts = f"{int(time.time())}.000003"
    reply_ts = f"{int(time.time())}.000004"
    # root
    requests.post(f"{API}/webhooks/slack/events",
                  json=_slack_event(root_ts, "TEST_SLACK: root for reply test"),
                  timeout=10)
    time.sleep(2)
    # reply in that thread
    r = requests.post(f"{API}/webhooks/slack/events",
                      json=_slack_event(reply_ts, "reply body", thread_ts=root_ts),
                      timeout=10)
    assert r.status_code == 200
    time.sleep(2)
    tickets = requests.get(f"{API}/tickets/", timeout=10).json()
    # no ticket should be created with slack_thread_ts == reply_ts
    reply_tix = [t for t in tickets if t.get("slack_thread_ts") == reply_ts]
    assert len(reply_tix) == 0, "Thread reply incorrectly created a ticket"


# ---------- Resolve slack ticket -> posts to slack ----------
def test_slack_ticket_resolve_posts_to_slack():
    ts = f"{int(time.time())}.000005"
    TEST_SLACK_TS.append(ts)
    requests.post(f"{API}/webhooks/slack/events",
                  json=_slack_event(ts, "TEST_SLACK: resolve flow test webhook error"),
                  timeout=10)
    time.sleep(3)
    tickets = requests.get(f"{API}/tickets/", timeout=10).json()
    mine = [t for t in tickets if t.get("slack_thread_ts") == ts]
    assert mine, "Slack ticket not created"
    tid = mine[0]["id"]
    r = requests.post(f"{API}/tickets/{tid}/resolve",
                      json={"latest_comment": "Fixed pincode config",
                            "resolution_notes": "Enabled pincode 560001"},
                      timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    # resolve returns {message, ticket}
    tk = data.get("ticket") or data
    assert tk.get("status") == "resolved"
    # validate via GET persistence
    got = requests.get(f"{API}/tickets/{tid}", timeout=10).json()
    assert got["status"] == "resolved"


# ---------- Seed an email ticket for analytics ----------
def test_create_email_ticket_for_analytics():
    payload = {
        "brand": "TEST_BrandA",
        "sender_email": "TEST_user@testbranda.com",
        "summary": "TEST_EMAIL: order creation failure for brand A",
        "full_message": "Details about order creation failure in the system",
        "source": "email",
    }
    r = requests.post(f"{API}/tickets/", json=payload, timeout=10)
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert data["source"] == "email"
    assert data["brand"] == "TEST_BrandA"


# ---------- Brand frequency endpoint ----------
def test_brand_frequency_email_only():
    r = requests.get(f"{API}/analytics/brand-frequency?source=email", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "success"
    assert data["source"] == "email"
    assert isinstance(data["data"], list)
    # Each item has brand+count
    for item in data["data"]:
        assert "brand" in item
        assert "count" in item
        assert isinstance(item["count"], int)
    # Must NOT contain slack tickets (which start with "#")
    slack_like = [b for b in data["data"] if str(b["brand"]).startswith("#")]
    assert not slack_like, f"Email brand-frequency leaked slack brands: {slack_like}"


# ---------- Source frequency endpoint ----------
def test_source_frequency_has_email_and_slack():
    r = requests.get(f"{API}/analytics/source-frequency", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "success"
    assert isinstance(data["data"], list)
    sources = {d["source"]: d["count"] for d in data["data"]}
    # After our seed above + slack tests, both should exist
    assert "email" in sources, f"email missing in {sources}"
    assert "slack" in sources, f"slack missing in {sources}"
    assert sources["slack"] >= 2
    assert sources["email"] >= 1


# ---------- Summary uses real data ----------
def test_analytics_summary_real_data():
    r = requests.get(f"{API}/analytics/summary", timeout=10)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "success"
    assert "summary" in d
    assert d["summary"]["total_issues"] >= 1
    assert isinstance(d["issues_by_client"], list)
    assert isinstance(d["issue_types"], list)
