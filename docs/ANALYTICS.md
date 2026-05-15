# OpsFlow Analytics – Metrics & Formulas

This document explains how each metric on the **Analytics Dashboard** is calculated, where the data comes from, and which filters are applied automatically.

> All analytics aggregations exclude **internal brands** (Blitznow, `#bug-reporting`, testing channels, etc.). The exclusion list is configured in `backend/filters/internal_clients.json` and applied via `_apply_internal_filter()` in `backend/services/ticket_service.py`.

---

## 1. Data Source

* **Collection:** MongoDB `tickets`
* **Date field used by all time-based filters:** `created_at` (UTC, ISO datetime)
* **Resolution field:** `resolved_at` + `tat_hours` (set on resolve)
* **Brand field:** `brand` (used as the “client” dimension)
* **Source field:** `source` — one of `email`, `slack`

---

## 2. Time Filters

Implemented in `backend/routes/analytics.py → parse_date_filter()`.

| UI Label   | `period` value | Window        | Implementation                  |
|------------|----------------|---------------|---------------------------------|
| 1 Week     | `1w`           | last 7 days   | `now - timedelta(days=7)`       |
| 1 Month    | `1m`           | last 30 days  | `now - timedelta(days=30)`      |
| 3 Months   | `3m`           | last 90 days  | `now - timedelta(days=90)`      |
| 6 Months   | `6m`           | last 180 days | `now - timedelta(days=180)`     |
| 1 Year     | `1y`           | last 365 days | `now - timedelta(days=365)`     |
| All Time   | `all` / blank  | no filter     | `start_date=None`               |

The filter compares `created_at >= start_date AND created_at <= now`. There is **no caching** – every analytics call queries Mongo live.

---

## 3. Metrics

### 3.1 Total Issues
**Formula:**
```
total_issues = COUNT(tickets) WHERE created_at IN [period] AND brand NOT IN internal_brands
```
Source: sum of all issue-type buckets returned by `get_issue_type_distribution()`.

### 3.2 Active Clients
```
total_clients = COUNT(DISTINCT brand) WHERE created_at IN [period] AND brand NOT IN internal_brands
```
Implementation: length of the `issues_by_client` array returned by `get_issues_by_client()`.

### 3.3 Top Issue Type
```
top_issue_type = ARGMAX(count) over GROUP BY issue_type
```
Computed by `get_issue_type_distribution()` (sorted desc on count) – the first entry.

### 3.4 Top Client
```
top_client = ARGMAX(total) over GROUP BY brand
```
Computed by `get_issues_by_client()` – the first entry after the internal-brand exclusion.

### 3.5 Average TAT (Turnaround Time)
Per ticket:
```
tat_hours = (resolved_at - created_at) in hours, rounded to 2 decimals
```
Set in `ticket_service.resolve_ticket()`.

Overall dashboard avg:
```
avg_tat_hours =
   Σ (avg_tat_hours_brand × resolved_count_brand)
   ───────────────────────────────────────────────
                Σ resolved_count_brand
```
This is a **weighted** average (so clients with more resolved tickets count more), implemented in `routes/analytics.py → get_analytics_summary()`.

### 3.6 Issue Distribution
```
distribution[issue_type] = COUNT(tickets) GROUP BY issue_type
percentage[issue_type]  = distribution[issue_type] / total_issues × 100
```
Returned by `get_issue_type_distribution()`. Percentages are computed on the frontend.

### 3.7 Issues by Client (with breakdown)
```
issues_by_client[brand].total    = COUNT(tickets) GROUP BY brand
issues_by_client[brand].by_type  = [ { issue_type, count } ... ]
```
Returned by `get_issues_by_client()`. Already excludes internal brands.

### 3.8 Issue Frequency Over Time (Time Series)
```
GROUP BY DATE(created_at)  →  { date: YYYY-MM-DD, count }
```
Returned by `get_time_series()`. Date string uses `$dateToString` in Mongo aggregation.

### 3.9 Brand Frequency (Email-only)
```
GROUP BY brand WHERE source = 'email' AND brand NOT IN internal_brands
```
Returned by `get_brand_frequency(source='email')`.

### 3.10 Source Distribution (Email vs Slack)
```
GROUP BY source WHERE brand NOT IN internal_brands
```
Returned by `get_source_frequency()`.

### 3.11 TAT by Client
```
For brand B where status = resolved AND tat_hours != null AND brand NOT IN internal_brands:
   avg_tat_hours = AVG(tat_hours)
   min_tat_hours = MIN(tat_hours)
   max_tat_hours = MAX(tat_hours)
   resolved_count = COUNT(*)
```
Returned by `get_tat_by_client()`.

### 3.12 TAT by Issue Type
Same as TAT by Client but grouped by `issue_type`. Returned by `get_tat_by_issue_type()`.

### 3.13 Resolution %
Currently derived on the dashboard:
```
resolution_pct = total_resolved / total_issues × 100
```
where `total_resolved = Σ resolved_count_brand` (from TAT-by-client).

---

## 4. Internal-Brand Exclusion

Defined in `backend/filters/internal_clients.json`.

A brand is considered INTERNAL if:
1. It exactly matches an entry in `internal_brands` (case-insensitive), OR
2. It contains any substring from `internal_brand_substrings`, OR
3. It matches an entry in `internal_slack_channels` (e.g., `#bug-reporting`), OR
4. It is `None` / empty string.

The exclusion is applied via the `$match` clause:
```javascript
{ $and: [
   { brand: { $nin: [null, ""] } },
   { brand: { $not: { $regex: "<patterns>", $options: "i" } } }
]}
```

To onboard a new internal/testing brand, **edit `filters/internal_clients.json`** and either restart the backend or call `filters.internal_clients.reload()`.

---

## 5. Refresh / Cache

* **No server-side cache.** Every API call hits Mongo.
* **Frontend** re-fetches when:
  - the page mounts (`useEffect` on `period`)
  - the period filter changes
  - the user navigates between Support ⇄ Analytics
* WebSocket events (`new_ticket`, `ticket_resolved`, `ticket_reopened`) on the Support dashboard also trigger a ticket re-fetch (analytics screen will reflect after navigation or period change).

---

## 6. Where the Code Lives

| Concern                       | File                                                                |
|-------------------------------|---------------------------------------------------------------------|
| Aggregation pipelines         | `backend/services/ticket_service.py` (`get_*` methods)              |
| HTTP endpoints                | `backend/routes/analytics.py`                                       |
| Internal-brand exclusion      | `backend/filters/internal_clients.py` + `internal_clients.json`     |
| Time-period parser            | `backend/routes/analytics.py → parse_date_filter()`                 |
| Frontend rendering            | `frontend/src/pages/AnalyticsDashboard.js`                          |
| API client                    | `frontend/src/services/api.js`                                      |
