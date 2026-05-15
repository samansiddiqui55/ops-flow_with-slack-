"""
Seed both Email AND Slack tickets so both analytics flows are visible.
Run: python seed_mixed.py
"""
import asyncio
import os
import random
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "opsflow_db")

BRANDS = ["Purplle", "Oziva", "Kapiva", "PUMA", "Atomberg", "Snitch", "Giva",
          "Decathlon", "Nykaa", "Truemeds", "Souled Store", "Mokobara", "NEWME"]

ISSUE_WEIGHTS = {
    "Pincode Serviceability": 25, "Webhook Issue": 12, "Order Creation Failure": 15,
    "Delay / TAT Issue": 10, "Alias Mapping": 10, "API / Integration Issue": 8,
    "Cost Policy Issue": 3, "Warehouse Issue": 5, "Shipment / AWB Issue": 7,
    "Panel / UI Issue": 2, "Other": 3,
}
SUMMARIES = {
    "Pincode Serviceability": ["Drop Pincode Not Serviceable - 400001", "Pincode 560034 showing non-serviceable", "NDD Pincode serviceability issue"],
    "Webhook Issue": ["Webhook not triggering for status updates", "Callback URL returning 500", "Webhook payload missing tracking info"],
    "Order Creation Failure": ["Orders not getting created in system", "Order sync failing from Unicommerce", "Bulk order upload failing"],
    "Delay / TAT Issue": ["Shipment stuck in transit for 5 days", "TAT exceeded - delivery delayed", "Multiple NDRs for same order"],
    "Alias Mapping": ["Incorrect warehouse alias mapping", "New alias mapping request for WH_BLR", "Alias not syncing with partner"],
    "API / Integration Issue": ["API timeout on order creation endpoint", "Invalid token error on auth", "500 error on tracking API"],
    "Cost Policy Issue": ["Cost policy not applied for new zone", "Commercial rate update needed", "Billing discrepancy on COD"],
    "Warehouse Issue": ["New warehouse setup required", "Scanning issue at Blitz warehouse", "WH capacity limit reached"],
    "Shipment / AWB Issue": ["AWB not generated for order", "Duplicate AWB assigned", "Wrong AWB on label"],
    "Panel / UI Issue": ["Unable to login to Blitz panel", "Dashboard not loading orders", "Filter not working on orders page"],
    "Other": ["General inquiry about service", "Account setup assistance needed", "Training request for new features"],
}
PRIORITIES = ["Highest", "High", "Medium", "Low"]
ASSIGNEES = ["Saman Siddiqui", "Ops Team", "Tech Support", None, None]


def weighted_issue():
    return random.choices(list(ISSUE_WEIGHTS), weights=list(ISSUE_WEIGHTS.values()), k=1)[0]


def make_ticket(source: str, idx: int):
    now = datetime.now(timezone.utc)
    days_ago = random.randint(0, 60)
    created_at = now - timedelta(days=days_ago, hours=random.randint(0, 23))
    brand = random.choice(BRANDS)
    issue_type = weighted_issue()
    summary = random.choice(SUMMARIES[issue_type])

    roll = random.random()
    if roll < 0.55:
        status = "open"
    elif roll < 0.80:
        status = "in-progress"
    else:
        status = "resolved"

    brand_slug = brand.lower().replace(" ", "")
    sender_email = (
        f"support@{brand_slug}.com"
        if source == "email"
        else f"slack-user-{random.randint(100, 999)}@slack.local"
    )

    resolved_at = None
    tat_hours = None
    if status == "resolved":
        tat_hours = round(random.uniform(2, 72), 2)
        resolved_at = created_at + timedelta(hours=tat_hours)

    ticket = {
        "id": str(uuid.uuid4()),
        "brand": brand,
        "sender_email": sender_email,
        "summary": summary,
        "full_message": f"Issue from {brand} ({source}):\n\n{summary}\n\nPlease investigate.",
        "source": source,
        "awb": f"AWB{random.randint(10000000, 99999999)}" if random.random() > 0.4 else None,
        "issue_type": issue_type,
        "status": status,
        "priority": random.choice(PRIORITIES),
        "assigned_to": random.choice(ASSIGNEES),
        "latest_comment": "Investigating" if status != "open" else "No comments yet.",
        "resolution_notes": "Resolved by ops team" if status == "resolved" else None,
        "jira_issue_key": f"TEC-{1000 + idx}",
        "jira_issue_id": str(100000 + idx),
        "jira_url": f"https://grow-simplee.atlassian.net/browse/TEC-{1000 + idx}",
        "slack_thread_ts": f"{int(created_at.timestamp())}.{random.randint(100000, 999999)}" if source == "slack" else None,
        "slack_channel_id": "C0B1JM61M8V" if source == "slack" else None,
        "resolved_at": resolved_at,
        "tat_hours": tat_hours,
        "created_at": created_at,
        "updated_at": created_at + timedelta(hours=random.randint(1, 48)),
    }
    return ticket


async def seed():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    coll = db["tickets"]

    await coll.delete_many({})
    print("Cleared tickets")

    tickets = []
    # 70 email tickets
    for i in range(70):
        tickets.append(make_ticket("email", i))
    # 30 Slack tickets
    for i in range(30):
        tickets.append(make_ticket("slack", 70 + i))

    await coll.insert_many(tickets)
    print(f"Inserted {len(tickets)} tickets (70 email + 30 slack)")

    # Stats
    by_source = {}
    by_status = {}
    for t in tickets:
        by_source[t["source"]] = by_source.get(t["source"], 0) + 1
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    print("By source:", by_source)
    print("By status:", by_status)
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
