# Openbridge workflows

End-to-end recipes for the four workflows users hit most. Each starts with
the question to ask the user, lists the tool sequence, and shows the calling
pattern. Adapt to Code Mode (`execute(...)`) or direct catalog calls based on
the deployment.

## Workflow 1 — Discover a table and (optionally) run a query

**Trigger phrases:** "I want to query…", "what tables exist for…", "run SQL
against…", "show me data from…".

### Step 0 — check capabilities (BEFORE you promise the user a query)

```python
caps = await get_capabilities()
query_enabled = caps.get("validate_query", {}).get("enabled", False)
```

`validate_query` and `execute_query` are **conditionally registered** on
the openbridge MCP — gated on `OPENBRIDGE_ENABLE_LLM_VALIDATION=true` AND a
sampling key (`FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY`). On default
deployments, neither is set and `get_capabilities` returns
`{"validate_query": {"enabled": false, "disabled_reason": "missing_sampling_key"}, ...}`
for both. **If the tools are disabled, your end-state for the user is the
schema plus the SQL string — they run it in their warehouse client
directly.** Don't promise execution you can't deliver.

Steps 1–3 below are **always available**. Steps 4–5 only run on the GREEN
path where `query_enabled` is `True`.

### Step 1 — find the product

```python
products = await search_products("orders")
# Use BARE 1-2 word terms. Multi-word product names and full phrases fail
# silently — "SP Orders", "Seller Partner orders", "Amazon orders" all
# return []. The em-dash in "Amazon Ads — Sponsored Products" also kills
# matching. The index does case-insensitive substring on the product name
# field; it's not fuzzy and it doesn't tokenize.
```

**Working query patterns (verified):**

| User asks for | Search term that works |
|---|---|
| SP Orders | `orders` |
| SP Finance | `finance` |
| SP Inventory | `inventory` |
| Amazon Advertising (any flavor) | `Amazon Advertising` or `amzn` |
| Google Analytics | `Google Analytics` |
| Shopify | `Shopify` |

**What does NOT work:** full product names (`"SP Orders"`,
`"Sponsored Products"`), em-dashes (`"Amazon Ads — Sponsored Products"`),
SDK / API version suffixes (`"... API v3"`). When in doubt, **try the
single most specific noun** (`orders`, `finance`, `inventory`) before
adding qualifiers.

### Step 2 — list tables for that product

```python
tables = await list_product_tables(product_id=50)
# Or filter to just what's enabled for one subscription:
tables = await list_product_tables(product_id=50, subscription_id=128853)
# returns: [{"name": "amzn_ads_sb_campaigns", "stage_id": 1004, "id": 2184}, ...]
```

When `subscription_id` is supplied, the list narrows to stages enabled for
that subscription. This is the right call when the user asks "what tables
does *my* pipeline produce?".

### Step 3 — get the schema (and the destination's actual table name)

```python
# IMPORTANT: get_table_schema accepts ONLY table_name. There is no
# product_id parameter — even though list_product_tables returned (id,
# name, stage_id) tuples, the schema lookup is name-only. Passing
# product_id raises a v1 validation error.
schema = await get_table_schema(table_name="amzn_ads_sb_campaigns")  # _master suffix optional
# returns: {fields: [...], rules: [...], destination: {"tablename": "..."}, ...}
```

The `_master` suffix is auto-stripped on lookup. **Read
`destination.tablename` from the schema to find the actual table name in
the customer's warehouse.** Destinations differ in their suffix
convention:

| Destination | Example `destination.tablename` |
|---|---|
| Redshift (typical) | `amzn_ads_sb_campaigns_master` |
| Per-product versioned | `sp_orders_v4`, `amzn_ads_sp_campaigns_v3` |
| Bare | `amzn_ads_sb_campaigns` |

Don't hardcode a suffix; quote what the schema says. The version number
in `_v4` / `_v3` is destination-meaningful — the customer's queries will
fail if you write `sp_orders_master` when the actual destination has
`sp_orders_v4`.

If multiple matches come back, the tool narrows to ones that end with the
exact name. Ambiguous matches return `None` with a warn-log — refine the
table name.

### Step 4 — validate, then gate execution on the decision (GREEN path only)

**Skip this step entirely if Step 0 reported `query_enabled = False`.**
Build the SQL, return it to the user with the schema, and tell them to run
it in their warehouse client. The MCP can't validate or execute it without
the conditional tools.

`validate_query` is not advisory — it is the **gate**. Read
`v["decision"]["allowed"]` and only call `execute_query` if it's `True`.
If `False`, surface the heuristic warnings to the user, fix the SQL, and
re-validate. Don't call `execute_query` "just to see what happens" — it
re-runs the same validator internally and will refuse anyway, but the
explicit conditional makes intent obvious and preserves the failing
diagnostic for the user.

```python
sql = "SELECT campaign_id, SUM(cost_7d) FROM amzn_ads_sb_campaigns_master WHERE date >= '2024-01-01' GROUP BY 1 LIMIT 100"
v = await validate_query(query=sql, key_name="key finance")
if not v.get("decision", {}).get("allowed", False):
    # Surface the validator's reasoning; do NOT call execute_query
    return {"validation_failed": v}
rows = await execute_query(query=sql, key_name="key finance")
return {"rows": rows[:50], "row_count": len(rows)}
```

Heuristic checks (always on): mutating-verb detection, `LIMIT` presence,
basic injection patterns. LLM-assisted review fires only when
`OPENBRIDGE_ENABLE_LLM_VALIDATION=true` AND
`FASTMCP_SAMPLING_API_KEY`/`OPENAI_API_KEY` is set.

To intentionally run an unbounded query, pass `allow_unbounded=True` to
**both** `validate_query` and `execute_query` — and flag the override to
the user explicitly so they're consenting on the record. Result of
`execute_query` is a list of row dicts; on error, a single-element list
`[{"error": ..., "status": ..., "details": ...}]`.

### Common pitfalls

- **Hardcoding `_master`** — the actual destination table name is in
  `destination.tablename` from `get_table_schema` output. `_master` is
  destination-specific (Redshift); `_v3` and the bare name appear on
  others. Read the schema, don't guess.
- **Promising query execution without checking capabilities** — see Step 0.
  On a default install, `validate_query` and `execute_query` aren't
  registered and calling them via `execute()` raises `Unknown tool`.
- **Hardcoding `key_name`** — it's the customer's storage key alias, not a
  fixed string. Ask if you don't have it.
- **Forgetting `LIMIT`** — the validator blocks unbounded queries unless
  you explicitly pass `allow_unbounded=True`.

## Workflow 2 — Schedule a historical / backfill job

**Trigger phrases:** "backfill", "historical job", "re-run data for…",
"create a one-off job", "ingest the last N days".

### Step 1 — confirm the subscription

```python
sub = await get_subscription_by_id(subscription_id="128853")
# Note: subscription_id is a STRING on this tool (and on update_subscription /
# cancel_subscription). It is INT on get_jobs / create_job. Mixed types are
# real on this server — confirm via get_schema(...) for any unfamiliar tool.
# Confirms product_id, status, stage_ids, storage attachment.
```

If `status != "active"`, surface that to the user — backfills against an
inactive subscription will queue but not run.

### Step 2 — get the right stage_ids

```python
stages = await get_product_stage_ids(product_id=sub["product_id"])
# Returns stage details with stage_id, name, schedule.
```

For partial backfills, pick only the stages the user actually needs.
**Wildcard backfills (every stage) burn upstream rate budget** — never default
to all stages without confirming.

### Step 3 — create the job

```python
job = await create_job(
    subscription_id=128853,
    date_start="2024-01-01",
    date_end="2024-01-07",
    stage_ids=[1004, 1005],
)
```

Date format is ISO `YYYY-MM-DD`. The MCP creates one job per (date, stage)
combination — a 7-day × 2-stage backfill is 14 jobs.

### Step 4 — monitor

```python
# get_jobs takes subscription_id as INT; is_primary is a STRING ("true"/"false").
jobs = await get_jobs(subscription_id=128853, is_primary="false")
# get_healthchecks takes subscription_id as STR; only filter_date for scoping.
checks = await get_healthchecks(subscription_id="128853", filter_date="2024-01-08")
```

Healthchecks surface ingest errors with `status`, `err_msg`, `error_code`.
Drill into a specific failure with
`get_history_by_id(history_id=<transaction_id>)`.

### Common pitfalls

- **`stage_ids` is required on the MCP**: `create_job` calls without it
  return a Pydantic validation error, not a wildcard backfill. The error
  surfaces as a *raised* exception inside `execute()` — see
  `error-envelope.md`. (On embed-cli, `--stage` is optional; omit and it
  *does* trigger a wildcard. CSV with a `stage_id` column is the safe
  CLI pattern.)
- Mixed-up date strings (`MM/DD/YYYY` or epoch). ISO `YYYY-MM-DD` only.
- Backfilling beyond the upstream's retention window. Healthchecks will
  surface "no data" errors that aren't really errors — confirm the source's
  retention before scheduling far back.

## Workflow 3 — Triage healthchecks

**Trigger phrases:** "why is my pipeline failing?", "show errors for
subscription…", "check the last N days of healthchecks", "what broke last
night?".

### Step 1 — list healthchecks for a date

```python
# subscription_id is a STRING on this tool; the only date filter is filter_date.
# There is NO last_days, NO page — pagination is internal (capped at 10 pages).
checks = await get_healthchecks(
    subscription_id="128853",
    filter_date="2024-01-15",
)
```

Filters supported by the MCP tool: `subscription_id` (`Optional[str]`) and
`filter_date` (`Optional[str]`, ISO `YYYY-MM-DD`). Without scoping,
healthchecks span the whole account and the response can be enormous —
always pass at least one filter.

For a multi-day window, iterate `filter_date` inside a single `execute()`
block, or fall back to embed-cli (`./bin/embed-cli health check
--last-days 7`) where the window flag is real:

```python
from datetime import date, timedelta
results = {}
end = date(2024, 1, 15)
for i in range(7):
    d = (end - timedelta(days=i)).isoformat()
    results[d] = await get_healthchecks(subscription_id="128853", filter_date=d)
return results
```

### Step 2 — group by `error_code` and `status`

When you have N healthcheck rows, summarize by `error_code` first. The
field is stable across runs and is what Openbridge support keys on. Flag
any `status: UNPROCESSED` rows that have been pending for more than a few
hours — that's usually a stuck transaction.

### Step 3 — drill into one transaction

```python
hist = await get_history_by_id(history_id=424242)
```

Gives you the full transaction record: `transaction_id`, `file_path`,
`err_msg`, `error_code`, `job_id`, timestamps. Cite all of these when
escalating.

### Step 4 — clear stuck transactions (with confirmation)

```python
result = await update_history_status(history_id=424242, status="cancelled")
```

This is destructive — it removes the transaction from the queue. Always
confirm with the user before calling. Status values commonly used:
`cancelled`, `failed`, `success`. Check the rules document for the table if
you're unsure what's accepted.

### Common pitfalls

- Reading `err_msg` and ignoring `error_code`. The string changes; the
  code doesn't. Quote the code when reporting.
- Calling `get_healthchecks` without `filter_date` and getting a wall of
  results spanning the whole account window.
- Passing `last_days=N` or `page=N` to the MCP `get_healthchecks` — those
  parameters do **not** exist on the MCP tool (only on the CLI). The call
  raises a parameter error inside `execute()`.
- Cancelling an `UNPROCESSED` transaction that's actively running. If the
  job started recently, wait a few minutes before cancelling — it may
  succeed.

## Workflow 4 — Manage subscriptions (pipelines)

**Trigger phrases:** "list my pipelines / subscriptions", "cancel the
shopify pipeline", "update subscription 12345 to…", "what storage does X
use?".

### List

```python
subs = await get_subscriptions(status="active")  # or "cancelled", "all"
storages = await get_storage_subscriptions()
```

Pagination is internal and capped at 10 pages — for accounts with >10 pages
of subscriptions, ask the user to filter by status or product. The
storage list is one call per linked storage; the MCP returns partial
results if individual storages fail.

### Inspect one

```python
# subscription_id is STRING on this tool (and on update / cancel).
sub = await get_subscription_by_id(subscription_id="128853")
```

Returns the JSON:API representation including `stage_ids`, `product_id`,
`status`, `storage_id`.

### Update / cancel

```python
updated = await update_subscription(
    subscription_id="128853",  # STRING
    attributes={"status": "active", "storage_group_id": 1289},
)
cancelled = await cancel_subscription(subscription_id="128853")  # STRING
```

`update_subscription` accepts the JSON:API `attributes` object — only pass
fields you want to change. `cancel_subscription` is sugar for `status:
cancelled`. Both are destructive in the sense that they affect production
ingestion — confirm before calling.

### Create

```python
created = await create_subscription(
    attributes={
        "product_id": 50,
        "remote_identity_id": 4832,
        "stage_ids": [1004, 1005],
        # ...full JSON:API attributes per the create schema
    },
)
```

The create payload is product-specific. Read the schema with
`get_schema("create_subscription")` before assembling — required fields
vary by product.

## Cross-cutting tips

- **Cite IDs.** Every recommendation should include `subscription_id`,
  `history_id` (when applicable), `error_code`, and the table name. Without
  these, support cannot triage.
- **Confirm before destructive calls.** `cancel_subscription`,
  `update_history_status`, `update_subscription` with status changes — read
  back the proposed action and pause.
- **Don't echo tokens.** Never print refresh tokens or JWTs in your
  response. Pull them from env, never from the user's prompt unless they
  explicitly paste one and ask you to use it.
- **Check `get_capabilities` once per session** if you're unsure what's
  enabled. It returns the active tool list, env-var requirements, and any
  opt-in flags so you don't ask the user to install something they already
  have.
