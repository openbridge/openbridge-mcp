# Code Mode — meta-tools and the sandbox

The Openbridge MCP runs Code Mode by default (`CODE_MODE=true`). In this mode
the only tools the client sees are `tags`, `search`, `get_schema`,
`get_schemas`, and `execute`. Every Openbridge operation — getting jobs,
running queries, creating subscriptions — happens inside a Python sandbox
called via `execute(code)`.

This file covers the four meta-tools, the sandbox limits, and the calling
patterns that actually work. Read this before writing any `execute()` block.

## Why Code Mode

The direct catalog has 25+ tools. Code Mode trades that surface for four
meta-tools the model can compose into multi-step plans without round-tripping
the MCP for each step. Two effects:

- One `execute()` block can chain "find table → get schema → validate SQL →
  run query" in a single call. No interleaved tool selection.
- The model only loads schemas it actually needs (via `get_schema`), keeping
  context focused.

The trade-off is that `execute()` is a sandbox — restricted stdlib, hard
timeout, hard memory cap. The patterns below stay inside those limits.

## The four meta-tools

### `tags()`

No arguments. **On today's openbridge MCP this returns a single
`untagged (N tools)` bucket** — the family-grouping is not yet populated by
the server. Treat `tags()` as a sanity / count check; **do not rely on it
for discovery** and do not invent family names like `jobs` or
`subscriptions` to present to the user as something `tags()` returned. Use
`search(query)` instead.

`CODE_MODE_INCLUDE_TAGS=false` disables this — assume `search` is always
available.

### `search(query)`

Fuzzy lookup over the tool catalog. Pass an intent phrase
(`"create historical job"`, `"validate sql"`, `"list amazon advertising
profiles"`). Returns matching tool names with one-line descriptions. The
catalog is small enough that a generic phrase usually returns the right tool
in the top three results.

### `get_schema(tool_name)` and `get_schemas([…])`

Returns the JSON Schema for one tool's input plus a brief description. Call
it before invoking a tool you haven't used in this session — the parameter
names and types are not memorizable across the catalog and the cost of a
wrong call (a bad job, a wrong cancel) is much higher than the cost of a
schema lookup.

`get_schemas([…])` batches several lookups into one round-trip — use it when
your `execute()` block will touch several tools.

### `execute(code)`

Runs Python in a sandbox. The code body has access to every Openbridge tool
as an `async` function with the same name as the catalog entry. `await` them.
The sandbox returns the value of the final expression (or whatever you
explicitly `return` from a wrapper function — see patterns).

## Sandbox limits

- **Timeout** — `CODE_MODE_MAX_DURATION_SECS` (default 30 s). Long backfills
  or large pagination loops can hit this. Page the work, return partial
  results, and let the user re-call.
- **Memory** — `CODE_MODE_MAX_MEMORY` (default 50 MB). Don't pull a million
  rows into a list — slice or aggregate.
- **Stdlib** — restricted. No `requests`, no `urllib`, no filesystem access,
  no subprocess. Network calls happen only through the Openbridge tool
  bindings.
- **Determinism** — no `random` seeding from outside, no clock-skew tricks.
  Tools that depend on time (date filters, retry windows) get the sandbox
  clock.

## Calling patterns that work

### Pattern 1 — single tool

```python
# inside execute()
jobs = await get_jobs(subscription_id=987, status="active", is_primary="true")
return jobs
```

Return the value as the final expression. The MCP serializes it back to the
caller. Note: `is_primary` is a **string** `"true"` / `"false"` per the
server schema, not a Python `bool` — and the parameter name is
`is_primary`, not `primary`.

> **ID typing across this catalog is mixed.** `subscription_id` on
> `get_jobs` / `create_job` is `int`; on `get_subscription_by_id` /
> `update_subscription` / `cancel_subscription` it's `str`.
> `remote_identity_id` on the `get_remote_identity*` tools is `str`; on the
> Amazon service tools (`get_amazon_api_access_token`,
> `get_amazon_advertising_profiles`) it's `int`. **Always**
> `await get_schema('<tool_name>')` for an unfamiliar tool — don't infer.

### Pattern 2 — schema-first when you're unsure

```python
schema = await get_schema("create_job")
# inspect schema.required, schema.properties to confirm parameter names
job = await create_job(
    subscription_id=987,
    date_start="2024-01-01",
    date_end="2024-01-07",
    stage_ids=[1004, 1005],
)
return job
```

When you're certain of the shape, skip the schema call.

### Pattern 3 — chained discovery + query

```python
# Find the right table, get its schema, then run a bounded query
products = await search_products("Amazon Ads Sponsored Brands")
product_id = products[0]["id"]

tables = await list_product_tables(product_id=product_id, subscription_id=128853)
target = next(t for t in tables if t["name"] == "amzn_ads_sb_campaigns")

schema = await get_table_schema(target["name"])
return {
    "table": target["name"],
    "fields": [f["name"] for f in schema.get("fields", [])][:20],
}
```

This is the pattern Code Mode is designed for: one block, multiple tool calls,
no client round-trips between them.

### Pattern 4 — capability-gated validate-then-execute

`validate_query` and `execute_query` are **conditionally registered**. They
are only available when the server has `OPENBRIDGE_ENABLE_LLM_VALIDATION=true`
AND a sampling key (`FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY`). On
default deployments, neither is set — calling them raises `Unknown tool`.
Confirm via `get_capabilities()` first, and have a fallback ready:

```python
caps = await get_capabilities()
query_enabled = caps.get("validate_query", {}).get("enabled", False)

# Always-available discovery
schema = await get_table_schema("amzn_ads_sb_campaigns")
sql = "SELECT campaign_id, sum(cost_7d) FROM amzn_ads_sb_campaigns_master WHERE date >= '2024-01-01' GROUP BY 1 LIMIT 100"

if not query_enabled:
    # Fallback: hand the user the SQL and the schema. They run it in their
    # warehouse client (Redshift, BigQuery, Snowflake, …) directly.
    return {
        "action_required": "manual_query",
        "reason": caps.get("validate_query", {}).get("disabled_reason", "query tools disabled"),
        "schema": schema,
        "sql": sql,
    }

# GREEN path — query tools are registered
v = await validate_query(query=sql, key_name="key finance")
if not v.get("decision", {}).get("allowed", False):
    return {"validation_failed": v}
rows = await execute_query(query=sql, key_name="key finance")
return {"rows": rows[:50], "row_count": len(rows)}
```

Always validate first when the tools *are* available — the validator
refuses mutating SQL, missing `LIMIT`, and obvious injection patterns
without spending an LLM call (LLM review is opt-in via
`OPENBRIDGE_ENABLE_LLM_VALIDATION=true`).

### Pattern 5 — scope to a single date

```python
# get_healthchecks on the openbridge MCP accepts only subscription_id and
# filter_date. There is NO last_days, NO page parameter — pagination is
# internal (capped at 10 pages). Scope by date instead.
checks = await get_healthchecks(
    subscription_id="128853",   # str on this tool
    filter_date="2024-01-15",   # specific ISO date
)
return checks
```

For multi-day surveys, either iterate over dates inside `execute()`:

```python
from datetime import date, timedelta
results = {}
end = date(2024, 1, 15)
for i in range(7):
    d = (end - timedelta(days=i)).isoformat()
    results[d] = await get_healthchecks(subscription_id="128853", filter_date=d)
return results
```

…or fall back to embed-cli, where `health check --last-days N` is a real
flag. The MCP path does **not** have a window parameter today.

## Sandbox stdlib is restrictive — assume blocked

The sandbox runs a small allowlist of stdlib modules. **The default
posture is: assume any non-trivial import is unavailable; verify in-session
before relying on it.** A few imports have been verified blocked, but the
list isn't exhaustive — many other modules are also blocked.

**Verified blocked:**

- Networking: `import requests`, `import urllib`, raw HTTP — only the
  Openbridge tool bindings reach the network.
- Filesystem: `open`, `pathlib.Path.write_text` — blocked. To return a
  CSV, build a string and let the caller render it.
- Process: `subprocess`, `os.system`, shelling out — blocked.
- Introspection: `traceback`, `inspect` — blocked.
- `time.sleep` for back-off — wastes the sandbox timeout. Read
  `_meta.retry_after_seconds` from a prior envelope and return it to the
  caller for them to retry.

**Other rules:**

- Modifying or shadowing the bound tool functions breaks subsequent calls
  — they're injected into the namespace; don't reassign them.
- Returning unserializable objects (sets, custom classes) breaks the
  serializer. Stick to dicts, lists, strings, numbers, booleans, None.
- `try/except` around `await tool(...)` may catch `pydantic.ValidationError`
  and similar, but exception propagation policy across the sandbox boundary
  is not stable. **Validate inputs via `get_schema` *before* the call**, not
  via try/except after — see `error-envelope.md` for the return-vs-raise
  table.

## When to drop out of `execute()`

You don't have to do everything in one block. Returning intermediate values
to the caller (the user-facing model session) and letting them re-call the
MCP is fine when:

- The user needs to see a list and pick one (table names, subscriptions).
- The next step depends on a decision the user has to make.
- You're about to do something destructive (`cancel_subscription`,
  `update_history_status`) and want explicit confirmation.

Code Mode is a tool, not a contract — use it when chaining genuinely saves
round-trips.

## Falling back to the direct catalog

`CODE_MODE=false` exposes every tool in
**`references/tools-catalog.md`** by name. Behavior is the same; you just
call `get_jobs(...)` directly instead of inside `execute()`. The startup logs
emit a WARNING in this mode — flag it to the user if you see it and ask
whether they want Code Mode back on.
