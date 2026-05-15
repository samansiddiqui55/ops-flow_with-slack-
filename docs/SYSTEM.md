# OpsFlow – System Documentation

End-to-end reference for OpsFlow: ticket lifecycle, integrations, polling, webhooks, DB schema, classification, and operational knobs.

> Status: post-CHANGE 1–8 (May 2026).

---

## 1. High-level Architecture

```
              ┌───────────────────────┐
   Email ───► │   Email Poller (60s)  │
              └──────────┬────────────┘
                         │
              ┌──────────▼────────────┐         ┌─────────────┐
   Slack ───► │   FastAPI Backend     │ ◄────►  │   MongoDB    │
   events     │  (server.py)          │         │   tickets    │
              └──┬────────┬───────────┘         │   mappings   │
                 │        │                     │   email_meta │
                 │        │                     └─────────────┘
                 │        │
                 ▼        ▼
              Jira API   React Dashboard (3000)
                         (WebSocket + REST)
```

Backend port: **8001** (internal).
Frontend port: **3000** (internal).
All HTTP traffic flows through Kubernetes ingress; backend routes are prefixed `/api`.

---

## 2. Email → Ticket Flow

1. `APScheduler.AsyncIOScheduler` (configured in `server.py`) fires `email_poller.process_emails()` **every 60 seconds**.
2. `email_service.fetch_new_emails()` runs in a thread executor (IMAP sync) and:
   * connects to Gmail `[Gmail]/All Mail` (fallback: `INBOX`)
   * uses **UID-based tracking** — `email_meta.last_processed_uid` ensures only NEW emails are picked up.
   * **CHANGE 2:** every email passes through `filters/email_filters.json` (allowlist beats blocklist).
3. For each accepted email:
   * `email_poller.process_single_email()` extracts brand, AWB, clean body.
   * Duplicate check via `mapping_service.get_email_mapping_by_thread()`.
   * `ticket_service.create_ticket()` creates a Mongo ticket and (best-effort) a Jira issue.
   * `mapping_service.create_email_mapping()` records the email-thread ↔ Jira mapping.
4. The new ticket is broadcast over WebSocket → dashboard refreshes.

**Polling frequency:** see `server.py`, the `scheduler.add_job(..., interval, seconds=60, ...)` call.

**First-run safety:** on cold start, `email_service` records the current max UID and skips ALL existing emails. Only emails received AFTER first boot are processed (prevents bulk-spam on import).

---

## 3. Slack → Ticket Flow

1. Slack Events API hits `POST /api/webhooks/slack/events`.
2. Signature check via `X-Slack-Signature` (HMAC SHA-256 over `v0:<ts>:<body>` using `SLACK_SIGNING_SECRET`).
3. URL-verification challenge handled inline.
4. `event_callback` with `event.type in {message, app_mention}` is queued to `BackgroundTasks._handle_slack_message`.
5. `_handle_slack_message`:
   * Skips bot/edited/deleted subtypes.
   * Channel filter: `SLACK_BUG_CHANNEL_ID` (preferred) or `SLACK_BUG_CHANNEL` name.
   * **Root message in channel** → create ticket (priority + assignee detection, dedupe by `slack_thread_ts`), reply in thread with rich operational card.
   * **Reply inside thread** (CHANGE 1):
       - Look up parent ticket by `slack_thread_ts`.
       - If parent is `resolved` → `ticket_service.reopen_ticket()` (status flip, Jira comment, activity event, WS broadcast).
       - If parent is open/in-progress → append `comment` event to `activity_history`, update `latest_comment`.
6. Slack acknowledgements posted via `slack_service.post_message()` (run in a thread executor).

**Realtime:** Slack uses the Events API webhook — no polling.

---

## 4. Jira Integration

### 4.1 Outbound (OpsFlow → Jira)
* `jira_service.create_issue()` creates a Task on `JIRA_PROJECT_KEY`.
* Failure is logged but does NOT block local ticket creation.
* `jira_service.add_comment()` is used for resolve/reopen sync.

### 4.2 Inbound (Jira → OpsFlow)
* `POST /api/webhooks/jira` receives `jira:issue_updated` and `issue_status_changed`.
* When the new status is `Done / Closed / Resolved`, the background task:
  - `_handle_jira_close()` updates the local Mongo ticket via `resolve_ticket_by_jira_key()`,
  - sends a resolution email through SMTP (if email mapping exists),
  - posts a Slack thread reply (if Slack mapping exists),
  - broadcasts `ticket_resolved` over WebSocket.

---

## 5. Dashboard Refresh

* The Support dashboard mounts a WebSocket to `wss://<host>/api/ws/tickets`.
* Events `new_ticket`, `ticket_resolved`, `ticket_reopened` trigger a re-fetch.
* **Polling fallback:** every 30 seconds the dashboard re-fetches `/api/tickets/`.
* The Analytics dashboard fetches on mount and on period change (no cache server-side).

---

## 6. Ticket Lifecycle

```
                  ┌──────────────────┐
       email/slack│      OPEN        │
       ─────────► │  (priority/type) │
                  └──┬────────┬──────┘
                     │        │
                     ▼        ▼
                in_progress  resolved ◄────────┐
                     │        │                │
                     │        │ new reply on   │
                     │        │ Slack thread   │
                     │        ▼                │
                     │     REOPENED (status=open)
                     │        │                │
                     └────────┴────────────────┘
```

Tickets transition based on:
* Dashboard `Resolve Ticket` button → `POST /api/tickets/{id}/resolve`
* Jira webhook → `_handle_jira_close()` → `resolve_ticket_by_jira_key()`
* **CHANGE 1:** Slack thread reply on a resolved ticket → `reopen_ticket()`

Every transition appends an entry to `tickets.activity_history`:
```json
{ "timestamp": "...", "event": "resolved|reopened|comment", "message": "...", "actor": "..." }
```

---

## 7. MongoDB Schema

Database: `${DB_NAME}` (default `test_database`).

| Collection            | Purpose                                                          |
|-----------------------|------------------------------------------------------------------|
| `tickets`             | Primary ticket records (see schema below)                        |
| `email_ticket_maps`   | email thread (`message_id`) ↔ Jira key mapping                   |
| `slack_ticket_maps`   | Slack `thread_ts` ↔ Jira key mapping                             |
| `email_meta`          | `last_processed_uid` for IMAP tracking                           |
| `processed_emails`    | message_id dedupe set (sync writes)                              |
| `brand_routing_configs` | optional brand→Jira routing config                             |
| `issue_logs`          | append-only event log (currently unused by hot path)             |

### `tickets` document
```jsonc
{
  "id": "uuid",
  "brand": "Mokobara",
  "sender_email": "support@mokobara.com",
  "summary": "Order delayed 5 days",
  "full_message": "<raw email/slack text>",   // never displayed directly
  "display_message": "<cleaned>",             // CHANGE 6, added at serialize time
  "source": "email|slack",
  "awb": "AWB12345678",
  "issue_type": "Delay / TAT Issue",          // CHANGE 5 hybrid classifier
  "status": "open|in-progress|resolved",
  "priority": "Critical|High|Medium|Low",
  "assigned_to": "Aarushi",
  "latest_comment": "...",
  "resolution_notes": "...",
  "resolved_by": "...",
  "resolved_at": "ISO datetime|null",
  "tat_hours": 12.5,
  "jira_issue_key": "TEC-1234",
  "jira_issue_id": "10456",
  "jira_url": "https://grow-simplee.atlassian.net/browse/TEC-1234",
  "slack_thread_ts": "1714752900.123456",
  "slack_channel_id": "C0123ABCD",
  "activity_history": [ /* CHANGE 1 */ ],
  "reopen_count": 0,
  "created_at": "ISO",
  "updated_at": "ISO"
}
```

`activity_history` and `reopen_count` are **optional** for backward compatibility — pre-existing tickets without these fields continue to work.

---

## 8. Configured Timings

| Concern                       | Value      | Source                                                |
|-------------------------------|------------|-------------------------------------------------------|
| Email polling                 | **60 s**   | `server.py → scheduler.add_job(... seconds=60 ...)`   |
| Dashboard polling fallback    | **30 s**   | `frontend/src/pages/SupportDashboard.js`              |
| WebSocket keepalive ping      | **30 s**   | `frontend/src/services/api.js`                        |
| Analytics cache               | **none**   | Live Mongo aggregations                               |
| Slack webhook                 | realtime   | Events API push                                       |
| Jira webhook                  | realtime   | Jira automation push                                  |
| First-run email skip          | one-shot   | `email_service._get_current_max_uid()`                |

---

## 9. Webhooks

| Endpoint                          | Caller          | Purpose                                |
|-----------------------------------|-----------------|----------------------------------------|
| `POST /api/webhooks/slack/events` | Slack           | message + app_mention events           |
| `POST /api/webhooks/jira`         | Jira automation | issue_updated / status changes         |
| `WS  /api/ws/tickets`             | Dashboard       | broadcast new/resolve/reopen events    |

Ingress: ngrok / managed ingress URL → service at port 8001.

---

## 10. Approval / Node Workflows (CHANGE 8)

**Untouched by the CHANGE 1–7 work.** Existing flows in the platform (Google Form automation, finance/data approvals, node creation, approval channels, webhook secrets) are not modified. Any further changes there must be explicit, isolated, and run through the same review checklist used here.

---

## 11. Environment Variables

Server-side (`backend/.env`):
```env
MONGO_URL=mongodb://localhost:27017
DB_NAME=test_database
CORS_ORIGINS=*

# Email (optional)
EMAIL_IMAP_SERVER=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=...
EMAIL_PASSWORD=...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_BUG_CHANNEL=bug-reporting
SLACK_BUG_CHANNEL_ID=Cxxxxx
SLACK_TAGGED_USER_IDS=U1,U2

# Jira
JIRA_BASE_URL=https://grow-simplee.atlassian.net
JIRA_EMAIL=...
JIRA_API_TOKEN=...
JIRA_WEBHOOK_SECRET=...
JIRA_PROJECT_KEY=TEC

# AI / classifier
EMERGENT_LLM_KEY=sk-emergent-...
ISSUE_CLASSIFY_USE_LLM=false   # CHANGE 5 opt-in
```

Frontend (`frontend/.env`):
```env
REACT_APP_BACKEND_URL=https://<host>.preview.emergentagent.com
WDS_SOCKET_PORT=443
```

---

## 12. Key files

| File                                          | Purpose                                    |
|-----------------------------------------------|--------------------------------------------|
| `backend/server.py`                           | FastAPI app + APScheduler + WebSocket      |
| `backend/jobs/email_poller.py`                | IMAP polling job (60 s)                    |
| `backend/services/email_service.py`           | IMAP/SMTP + filtering                      |
| `backend/services/slack_service.py`           | Slack Bot API wrapper                      |
| `backend/services/jira_service.py`            | Jira REST wrapper                          |
| `backend/services/ticket_service.py`          | Ticket CRUD + analytics + REOPEN/RESOLVE   |
| `backend/services/mapping_service.py`         | email/slack ↔ jira mapping CRUD            |
| `backend/routes/webhooks.py`                  | Slack + Jira webhook handlers              |
| `backend/routes/tickets.py`                   | REST CRUD + resolve/reopen                 |
| `backend/routes/analytics.py`                 | Analytics endpoints                        |
| `backend/models/ticket.py`                    | Ticket model + classifier                  |
| `backend/filters/email_filters.json`          | CHANGE 2 — junk email config               |
| `backend/filters/internal_clients.json`       | CHANGE 3 — analytics exclusion             |
| `backend/utils/message_cleaner.py`            | CHANGE 6 — display_message builder         |
| `docs/ANALYTICS.md`                           | CHANGE 4                                   |
| `docs/ISSUE_CLASSIFICATION.md`                | CHANGE 5                                   |
| `docs/SYSTEM.md`                              | CHANGE 7 (this file)                       |

---

## 13. Final Checklist (CHANGE 1–8)

- [x] Slack tickets working (root message → ticket → thread ack)
- [x] Email tickets working (poll + filter + Jira best-effort)
- [x] Jira creation working
- [x] Dashboard live updates working (WS + 30s poll fallback)
- [x] Analytics working (real clients only, accurate time filters)
- [x] Resolve → reopen working (Slack thread reply + REST `/reopen`)
- [x] Existing node workflows untouched
- [x] Existing approvals untouched
