# OpsFlow Issue Classification

This document explains how OpsFlow assigns an **issue_type** to every newly created ticket (email or Slack source), the keyword rules, the optional LLM fallback, and how to extend the system.

> Implementation: `backend/models/ticket.py` → `classify_issue_type()` (rule-based) and `classify_issue_type_hybrid()` (rule + optional LLM).
>
> Invocation: `backend/services/ticket_service.py → create_ticket()` calls `classify_issue_type_hybrid()`.

---

## 1. Why classification was changed (CHANGE 5)

**Problem observed:** the original rule list was a top-down `if "kw" in text` cascade. Because **Delay / TAT Issue** included broad keywords like `delay`, `delayed`, `late`, `pending`, `stuck`, **most tickets that mentioned any of those generic words bucketed into Delay/TAT** — even when the actual issue was about pincode, alias mapping, etc.

**Fix:**

1. Switched to a **weighted, scored** matcher. Each rule is now a regex with a numeric weight.
2. **Subject hits weigh 2× body hits** — subject is intent.
3. **Delay / TAT Issue moved to the LAST rule** and only fires on logistics-specific phrases: `delivery delay`, `shipment delay`, `tat exceeded`, `stuck in transit`, `delayed by N days/hours`, `NDR`, etc.
4. A minimum signal threshold of `score >= 2` is required; otherwise → `Other`. Generic words like `pending`, `late` no longer dominate.
5. **Optional LLM fallback** runs only when the keyword pass returns `Other` AND `ISSUE_CLASSIFY_USE_LLM=true` in `.env`.

---

## 2. Categories

Defined in `models/ticket.py → ISSUE_TYPES`:

```
- Pincode Serviceability
- Webhook Issue
- Order Creation Failure
- Delay / TAT Issue
- Alias Mapping
- API / Integration Issue
- Cost Policy Issue
- Warehouse Issue
- Shipment / AWB Issue
- Panel / UI Issue
- Other
```

---

## 3. Algorithm

```text
Input: subject, body
For every (category, [(pattern, weight), ...]) rule:
    score(category) = 0
    For each (pattern, weight):
        if regex match in subject: score += weight * 2
        elif regex match in body:  score += weight
Pick the category with the highest score.
If max score < 2 -> "Other"
```

Patterns use word boundaries (`\b`) and short multi-word phrases so common nouns don't cause misclassification.

### Example weights (excerpt)
| Category                  | Phrase                                | Weight |
|---------------------------|----------------------------------------|--------|
| Pincode Serviceability    | `not serviceable`                      | 4      |
| Pincode Serviceability    | `pincode`                              | 3      |
| Webhook Issue             | `webhook not triggered`                | 5      |
| Order Creation Failure    | `order not created`                    | 5      |
| Alias Mapping             | `alias mapping`                        | 5      |
| API / Integration Issue   | `api timeout`                          | 5      |
| Shipment / AWB Issue      | `awb not generated`                    | 5      |
| Panel / UI Issue          | `dashboard not loading`                | 5      |
| Delay / TAT Issue         | `delivery delay`                       | 5      |

Full list lives in `classify_issue_type()`.

---

## 4. Hybrid LLM Fallback

When `classify_issue_type()` returns `Other`, `classify_issue_type_hybrid()` checks env flags:

```env
ISSUE_CLASSIFY_USE_LLM=true        # opt-in
EMERGENT_LLM_KEY=sk-emergent-...   # required for LLM
```

If both are present, it calls **Claude Haiku 4.5** via `emergentintegrations`:

> System: “You are an ops-issue classifier for a logistics platform. Pick exactly ONE category from: [...]. Reply with ONLY the category name.”
> User: `Subject: ...\n\nBody: ...\n\nCategory:`

The returned text is matched (case-insensitive substring) against `ISSUE_TYPES`. On any failure → returns the keyword result (`Other`).

**Default is OFF** so existing behaviour is unchanged.

---

## 5. How to extend / tune

### 5.1 Add a category
1. Append the new category name to `ISSUE_TYPES`.
2. Add a `(category, [(pattern, weight), ...])` entry to the `rules` list in `classify_issue_type()`.
3. Restart backend.

### 5.2 Reduce false positives for an existing category
* Increase pattern specificity (longer multi-word regex)
* Lower the keyword weight
* Move the rule LATER in the list (when scores tie, earlier wins is irrelevant because we take max, but ordering still helps when adding very generic regexes)

### 5.3 Enable LLM fallback (production)
```env
ISSUE_CLASSIFY_USE_LLM=true
EMERGENT_LLM_KEY=sk-emergent-XXXX
```

### 5.4 Verifying changes
```bash
cd /app/backend
python3 -c "
from models.ticket import classify_issue_type
print(classify_issue_type('Order delayed 5 days', 'shipment stuck in transit'))
"
```

---

## 6. Logs

Every classification is logged once at ticket-create time:
```
INFO ticket_service - Classified issue_type=<X> priority=<Y> assignee=<Z>
```

For LLM fallback failures (when enabled), the LLM call is silently downgraded to the keyword result — see `classify_issue_type_hybrid()`.
