# OpsFlow — Product Requirements (Living Doc)

## Original Problem (latest iteration)
Add real-ops Slack workflow on top of existing OpsFlow (Email→Ticket→Jira) without
touching ticket creation, Jira service, analytics, or email→ticket flow:
- Listen ONLY to #bug-reporting
- Convert message → ticket only if it has a shipment-issue keyword
  (aging/sync/shipment/delay/stuck/dispatch/delivery/NDR/RTO) AND tags an ops user
  (Siddiqui/Arushi/Deepak Singh)
- Skip if already handled (✅ reaction, resolution-keyword reply, existing mapping)
- Ignore bots, edited/deleted messages, thread replies, Slack retries
- Reuse existing email→Jira logic (no duplicate Jira creation)
- ✅ reaction → auto-resolve dashboard ticket (and reply in same Slack thread)
- Dashboard "Resolve Ticket" click for source=email tickets → auto-send resolution
  email to original brand email thread

## Architecture
- FastAPI + Motor (Mongo) backend, React frontend, supervisor-managed
- Background tasks for Slack/Jira/Email handling
- WebSocket /api/ws/tickets for live dashboard updates
- Email poller (1-minute cycle, UID-based) + historical-import endpoint
- Jira webhook + Slack events webhook both at /api/webhooks/...

## What's Implemented (2026-04-29)
- **Slack workflow gating** (`backend/routes/webhooks.py`):
  - `_has_issue_keyword` — keyword-list filter (env: SLACK_ISSUE_KEYWORDS)
  - `_has_required_tag` — tagged-user filter via Slack ID (SLACK_TAGGED_USER_IDS),
    with display-name fallback (SLACK_TAGGED_USER_NAMES, default
    "Siddiqui,Arushi,Deepak Singh")
  - `_is_already_handled` — checks existing mapping + ✅ reaction on root
    + resolution-keyword reply in thread (SLACK_RESOLUTION_KEYWORDS)
  - Slack retry header (`X-Slack-Retry-Num`) → fast 200 ack, no reprocess
  - `reaction_added` event handler — ✅ on root → `ticket_service.resolve_ticket`
- **Slack helpers** (`services/slack_service.py`): `get_thread_replies`,
  `get_message_reactions`
- **Email reply on dashboard resolve** (`services/ticket_service.py`):
  - `resolve_ticket` now sends resolution email when `source=='email'`, reusing
    `email_service.send_email`, `mapping_service.get_email_mapping_by_jira` for
    `In-Reply-To` threading + CC list, and `format_resolution_email`
- **Config** (`backend/config.py`): added 5 new env vars (above)
- **Tests** at `backend/tests/test_slack_webhook_resolution.py` — 22/22 passing
  against the public preview URL

## Personas
- Ops engineer in #bug-reporting Slack channel — reports shipment issues by tagging
  Siddiqui/Arushi/Deepak Singh
- Support agent on dashboard — clicks "Resolve" to close tickets and triggers
  thread reply (Slack) or email reply (Email) automatically

## Required env vars (production)
- `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` (Slack app: chat:write, channels:history,
  groups:history, reactions:read, users:read, users:read.email)
- `SLACK_BUG_CHANNEL_ID` (preferred over name) and/or `SLACK_BUG_CHANNEL` (default
  `bug-reporting`)
- `SLACK_TAGGED_USER_IDS` (comma-separated Slack IDs of Siddiqui/Arushi/Deepak Singh)
- Optional overrides: `SLACK_TAGGED_USER_NAMES`, `SLACK_ISSUE_KEYWORDS`,
  `SLACK_RESOLUTION_KEYWORDS`
- Existing: `EMAIL_USERNAME/PASSWORD/IMAP/SMTP`, `JIRA_BASE_URL/EMAIL/API_TOKEN/PROJECT_KEY`

## Slack App setup notes
- Subscribe to bot events: `message.channels`, `message.groups` (for private),
  `app_mention`, `reaction_added`
- Request URL: `https://<host>/api/webhooks/slack/events`
- Add bot to #bug-reporting channel
- (Optional) Use Socket Mode if you don't want to expose a public URL

## Backlog (not yet done)
- P1: hide WARN log spam when SLACK_SIGNING_SECRET unset (log once at startup)
- P2: expose DELETE /api/tickets/{id} for test cleanup
- P2: anchor mention regex with `^U` for stricter Slack-ID parsing
- P2: consider deduping Slack tickets only by `slack_thread_ts`
  (avoid running the email-style sender+subject dedupe on slack tickets)
