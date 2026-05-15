# OpsFlow Changelog — CHANGE 1 through 8

All changes were applied **incrementally**, **backward-compatibly**, and **without rewriting unrelated code**.

---

## CHANGE 1 — Reopen resolved tickets on new Slack thread reply
**Files touched**
- `backend/models/ticket.py` — added `activity_history: list` and `reopen_count: int` (optional, defaults preserved).
- `backend/services/ticket_service.py` — new `append_activity()` and `reopen_ticket()`; resolve flow now appends an activity entry.
- `backend/routes/webhooks.py` — `_handle_slack_message` now branches on thread replies: if parent ticket is `resolved`, calls `reopen_ticket()` and acknowledges in thread; otherwise appends a `comment` activity.
- `backend/routes/tickets.py` — new `POST /api/tickets/{id}/reopen` REST endpoint.
- `frontend/src/components/TicketDetail.js` — renders `Activity History` section + reopen count.
- `frontend/src/App.css` — styles for activity timeline.

**Why:** previously a resolved ticket stayed closed even when the customer replied; ops missed re-escalations.

**Tested:** `POST /resolve` → `POST /reopen` returns `status=open`, `reopen_count=1`, activity history of 3 entries (resolved → reopened → comment).

---

## CHANGE 2 — Junk / promotional email filter
**Files touched**
- `backend/filters/email_filters.json` — editable blocklist (domains, sender patterns, subject keywords, body keywords) + allowlist.
- `backend/filters/email_filters.py` — loader + `should_process_email()` helper.
- `backend/services/email_service.py` — `is_valid_sender`, `is_valid_subject`, and new `is_valid_email()` now consult the centralized filter (legacy patterns kept).
- `backend/services/email_service.py` (`fetch_new_emails`) — final body-keyword check added before ticket creation.

**Why:** Naukri / Magicbricks / NPTEL / job-alerts / verification / marketing / PG ads / newsletters were polluting the dashboard.

**Tested:** filter rejects naukri/magicbricks/nptel/hr@/noreply@ + allowlist (`samansiddiqui903@gmail.com`, `grow-simplee.com`) always wins.

---

## CHANGE 3 — Analytics cleanup & accuracy
**Files touched**
- `backend/filters/internal_clients.json` — editable list (Blitznow, `#bug-reporting`, testing, etc.).
- `backend/filters/internal_clients.py` — exposes `get_internal_match_filter()`.
- `backend/services/ticket_service.py` — all analytics aggregations (`get_brand_frequency`, `get_source_frequency`, `get_issues_by_client`, `get_issue_type_distribution`, `get_time_series`, `get_tat_by_client`, `get_tat_by_issue_type`) merge the internal-clients $and clause.
- Time filters: verified 1w=7d, 1m=30d, 3m=90d, 6m=180d, 1y=365d (already correct in `routes/analytics.py`).

**Tested:** after seeding Blitznow + `#bug-reporting` + Testing tickets, analytics excludes them; only real client `Mokobara` appears.

---

## CHANGE 4 — Analytics documentation
**Files added**
- `docs/ANALYTICS.md`

Formulas covered: total issues, top issue type, active clients, top client, average TAT (weighted), issue-type %, time series, brand frequency, source distribution, TAT by client / issue type, resolution %.

---

## CHANGE 5 — Issue type classification audit
**Files touched**
- `backend/models/ticket.py` — rewrote `classify_issue_type()` as a **weighted regex scorer**; subject hits worth 2×; **Delay / TAT moved to last** and requires logistics context (`delivery delay`, `shipment stuck`, `tat exceeded`, …).
- `backend/models/ticket.py` — new `classify_issue_type_hybrid()` with **optional** LLM fallback (Claude Haiku via Emergent LLM key).
- `backend/services/ticket_service.py` — `create_ticket()` now awaits the hybrid classifier.
- `backend/.env` + `backend/config.py` — `ISSUE_CLASSIFY_USE_LLM` (default `false`).
- `docs/ISSUE_CLASSIFICATION.md`

**Why:** previously most tickets bucketed into Delay/TAT because generic words like `delay`, `pending`, `stuck` matched first.

**Tested:** “Pending” → Other, “shipment stuck in transit, delivery delay” → Delay/TAT, “awb not generated” → Shipment/AWB.

---

## CHANGE 6 — Cleaner Full-Message dashboard panel
**Files touched**
- `backend/utils/message_cleaner.py` — strips RFC822 headers, mailto, quoted replies, signatures, HTML, browser-view footers; preserves the human body.
- `backend/services/ticket_service.py` — `serialize_ticket()` adds `display_message` alongside (raw `full_message` untouched in DB).
- `frontend/src/components/TicketDetail.js` — renders `display_message` with fallback to `full_message`.

**Tested:** sample email with `From:/To:/Cc:/Subject:` headers, signature, `mailto:` and quoted reply → output contains only the human paragraph.

---

## CHANGE 7 — System documentation
**Files added**
- `docs/SYSTEM.md` — full architecture, Slack/Email/Jira flows, polling timings (60s email / 30s dashboard fallback), MongoDB schema, ticket lifecycle, webhook list, env vars, file index, final checklist.
- `docs/README.md` — table-of-contents.

---

## CHANGE 8 — Preserve node/approval automation
**No code touched.** Existing node creation, Slack approvals, Google Form automation, finance/data/node approvals, webhook secrets, and approval channels are untouched. Verified by inspecting the codebase: there is no node/approval module in scope of the current changes.

---

## Final checklist
- [x] Slack tickets working
- [x] Email tickets working
- [x] Jira creation working
- [x] Dashboard working
- [x] Analytics working
- [x] Resolved → reopen working
- [x] Existing node workflows untouched
- [x] Existing approvals untouched
