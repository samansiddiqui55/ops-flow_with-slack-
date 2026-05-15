# OpsFlow – PRD

## Original problem statement
Modify the existing OpsFlow system (Slack → Ticket → Jira → Dashboard → Analytics → Email → Node workflows) with 8 changes without breaking existing functionality. See `/app/docs/CHANGELOG.md` for the full request.

## Architecture
- **Backend:** FastAPI (Python 3.11), Motor (Mongo async), APScheduler, imap-tools, slack_sdk, jira.
- **Frontend:** React 19 + Chart.js + WebSocket.
- **Datastore:** MongoDB (`tickets`, `email_ticket_maps`, `slack_ticket_maps`, `email_meta`).
- **Scheduling:** AsyncIOScheduler on the FastAPI event loop (60s email poll).

## User personas
- **Ops engineer** – resolves logistics tickets, monitors dashboard.
- **Analytics user** – reads metrics; needs clean per-client data.
- **Admin** – tweaks blocklists/allowlists via JSON.

## Core requirements (static)
1. Email→Ticket / Slack→Ticket flows must continue to work.
2. Resolved tickets must reopen on new Slack reply.
3. Junk/promotional emails (Naukri, Magicbricks, NPTEL, etc.) must not become tickets.
4. Analytics must exclude internal/test brands; time filters must be precise.
5. Issue type classification must be accurate (no over-Delay/TAT bucketing).
6. Full Message panel must show readable summary, not raw headers.
7. Documentation must explain analytics formulas + the whole system.
8. Node automation, approvals, Google Form, finance/data approvals untouched.

## What's been implemented (Jan 2026)
- **CHANGE 1** – Reopen flow: `reopen_ticket()` service, `POST /api/tickets/{id}/reopen`, Slack thread-reply branch, activity_history + reopen_count, dashboard timeline UI.
- **CHANGE 2** – `filters/email_filters.json` + loader, allowlist/blocklist applied in `email_service`.
- **CHANGE 3** – `filters/internal_clients.json` + `_apply_internal_filter()` merged into all analytics aggregations.
- **CHANGE 4** – `/app/docs/ANALYTICS.md` with all formulas.
- **CHANGE 5** – Weighted regex classifier (Delay/TAT moved last, requires logistics context), optional Emergent LLM fallback (`ISSUE_CLASSIFY_USE_LLM`).
- **CHANGE 6** – `utils/message_cleaner.py` + `display_message` field; UI renders cleaned text and falls back to raw.
- **CHANGE 7** – `/app/docs/SYSTEM.md`, `/app/docs/README.md`, `/app/docs/CHANGELOG.md`.
- **CHANGE 8** – No code touched (no node/approval module in scope).

## Testing
Backend: **15/15 passed** (`/app/test_reports/iteration_4.json`). Verified resolve/reopen, junk filter, analytics exclusion, period filters, hybrid classifier, display_message, WebSocket broadcasts.

## Prioritized backlog (next)
- P1: Wire real Slack/Jira/Email credentials and run end-to-end.
- P2: Expose `DELETE /api/tickets/{id}` for admin cleanup.
- P2: Remove commented-out legacy code in `server.py` and `routes/tickets.py`.
- P3: Log LLM fallback failures at WARN; return 400 for unknown analytics `period` values.
- P3: Activity-history pagination / collapse on UI when count grows.

## Files
See `/app/docs/CHANGELOG.md` for per-change file list.
