# OpsFlow — Product Requirements Document

## Original Problem Statement
Frontend dashboard was not showing tickets even though backend was creating them correctly (Slack → Jira flow working, MongoDB storing tickets, /api/tickets/ returning data). The Support Dashboard said "No tickets found" and Analytics was blank because frontend was hitting `localhost:3000/api/tickets/` (Express dev-server) instead of the FastAPI backend. User wanted to see tickets from BOTH email AND Slack sources without changing backend logic.

## Architecture
- **Backend**: FastAPI on port 8001 (supervisor-managed) with MongoDB (Motor async)
- **Frontend**: React 19 + CRACO (CRA) on port 3000, Chart.js + Lucide icons
- **Routing**: Kubernetes ingress routes `/api/*` → backend:8001, `/*` → frontend:3000, both behind preview URL
- **Integrations**: Jira REST (TEC project), Slack SDK (bug-reporting channel), IMAP email poller (APScheduler)

## User Personas
- **Logistics Ops Engineer** — triages incoming Slack/email issues, resolves in dashboard, syncs to Jira
- **Support Lead** — tracks team analytics: per-client/issue-type frequencies, avg TAT, source mix
- **Brand Account Manager** — monitors a single brand's issue trends over time

## Core Requirements (Static)
1. Tickets ingested from Slack channel + IMAP email → auto-created in Mongo + Jira
2. Dashboard lists tickets with filter/sort/search and ticket-detail side panel
3. Resolve action posts back to Slack thread + Jira comment
4. Analytics: per-client, per-issue-type, time-series, brand frequency, source mix (email vs slack), TAT
5. Real-time updates via WebSocket with 30s polling fallback

## What's Been Implemented (2026-05-06)
- Restored backend codebase from user-provided zip; installed missing deps (pydantic-settings, imap-tools, APScheduler, jira, slack_sdk)
- Created `/app/frontend/.env` with `REACT_APP_BACKEND_URL` → preview URL (root cause of "No tickets found")
- Updated `/app/backend/.env` with user's Jira/Slack creds; CORS_ORIGINS=`*`
- Created `/app/backend/seed_mixed.py` to seed 100 demo tickets (70 email + 30 slack across 13 brands, weighted issue types, realistic TAT, jira keys TEC-1000..TEC-1099)
- Verified backend `/api/health`, `/api/tickets/`, `/api/analytics/*` endpoints
- Verified frontend renders Support Dashboard (ticket cards + detail panel) and Analytics Dashboard (5 summary cards + 5 charts)
- Testing agent confirmed 100% pass on all 16 acceptance criteria

### Bug Fix: Slack thread reply broken when email poller active (2026-05-06)
- **Root cause**: `EmailPollerJob` ran in a separate thread with its own `asyncio.new_event_loop()`. Motor's `AsyncIOMotorClient` (created at module import) and `ws_manager` are bound to the FastAPI loop. Calling `ticket_service.create_ticket` from the foreign loop caused Motor / async broadcast / sync-Slack-SDK calls to silently hang in concurrent FastAPI request handlers — Slack reply line was never reached.
- **Fix**:
  - Replaced threaded poller with **APScheduler `AsyncIOScheduler`** running on the FastAPI main loop (`/app/backend/server.py` startup_event, max_instances=1, coalesce=True, 60s interval)
  - Scheduler only starts when `EMAIL_USERNAME` is configured
  - IMAP fetch (sync) offloaded via `loop.run_in_executor` so it never blocks the loop
  - `slack_service` blocking `slack_sdk` calls (`chat_postMessage`, `users_info`, `conversations_info`, `chat_getPermalink`) now run via `run_in_executor` with explicit success/failure logs (`[SLACK] reply success` / `[SLACK] reply API error`, `exc_info=True`) — no silent swallows
- **Verified**: Direct sim showed Slack `post_message` → `ok=True` (Jira `TEC-99` created, reply posted) on the same loop while scheduler was active.

## Backlog (P0/P1/P2)
- **P1** Add `data-testid` to ticket cards, search box, sort/filter buttons (testability)
- **P1** Disable 30s polling when WebSocket is connected (avoid double-fetch)
- **P2** Cleanup `server.py` legacy commented blocks (200+ lines)
- **P2** Configure email IMAP creds in `.env` so live email polling resumes
- **P2** Per-brand drill-down view from Analytics → Support filtered list
- **P2** Bulk-resolve, ticket assignment dropdown, comment thread view

## Next Tasks
- Wire real Slack/Email creds for live ticket creation
- Add CSV export on Analytics dashboard
- Auth layer (currently unauthenticated)
