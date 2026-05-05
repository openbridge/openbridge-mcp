---
name: openbridge-mcp
description: >
  Drive the Openbridge platform through the Openbridge MCP server (FastMCP,
  Code Mode default) or the embed-cli. Use this skill whenever the user
  mentions Openbridge, openbridge-mcp, embed-cli, refresh tokens against
  api.openbridge.io, or asks about Openbridge jobs, pipelines, subscriptions,
  storages, healthchecks, rules, tables / payloads, _master tables, product
  stages / stage_ids, historical or backfill jobs, remote identities, or
  running validated SQL via the Query API. Also trigger when the user wants
  to discover what tables a subscription produces, search products (e.g.
  "Amazon Advertising"), inspect the rules document for a table,
  parse error envelopes / `error_kind` / `_meta.rate_limit`, or compare Code
  Mode meta-tools (`tags`, `search`, `get_schema`, `execute`) to the direct
  tool catalog. If a conversation touches Openbridge pipelines or warehouse
  tables, prefer this skill — wrong tool names or stage_ids waste API quota.
version: "0.1.2"
mcp_servers: mcp-servers.json
compatibility:
  - openbridge-mcp >= 1.0 (Code Mode tools tags/search/get_schema/execute, FastMCP HTTP transport)
  - embed-cli (shell/Docker fallback when MCP isn't attached)
---

# Openbridge MCP

## What this skill is for

The Openbridge platform runs the customer's data pipelines: a **subscription**
(also called a "pipeline") collects data from a **product** (Amazon Ads,
Google Analytics, Shopify, …) into the customer's storage. Each product
exposes one or more **stages** (specific reports or payloads), and each stage
lands as a **table** in storage (often suffixed `_master`). The **Rules API**
catalogs every table's schema. The **Query API** runs validated SQL against
those tables. **Healthchecks** surface ingestion errors. **Jobs** schedule
historical/backfill data pulls.

You drive all of that either through:

1. **Openbridge MCP** — the FastMCP server in `openbridge-mcp/`. Default mode
   is Code Mode: clients see only `tags`, `search`, `get_schema`/`get_schemas`,
   `execute` and run Python in a sandbox that calls individual tools. This is
   the recommended path. Read **`references/code-mode.md`** before writing any
   `execute()` block.
2. **embed-cli** — shell/Docker CLI (`./bin/embed-cli` or
   `openbridge/embed-cli` Docker image). Same APIs, no sandbox. Use when the
   MCP server isn't attached or for batch CSV jobs.

If the user has the MCP attached, **prefer the MCP**. Drop to embed-cli only
when explicitly asked or when a workflow needs `jobs batch -f <csv>`.

## When to use this skill

Trigger on any of:

- The user mentions Openbridge, the Openbridge MCP, or the embed-cli
- They reference subscriptions / pipelines / storages, healthchecks,
  remote identities, jobs, products, stages, or rules / `_master` tables
- They want to run SQL against an Openbridge-managed table or warehouse
- They want to backfill / re-run historical data, or cancel/update a
  subscription
- They paste a refresh token shaped `xxx:yyy` or an Authorization Bearer
  header pointing at `*.api.openbridge.io`
- They mention error envelopes, `error_kind`, `_envelope_version`,
  `_meta.rate_limit`, `_meta.normalized`, or other contract fields

When in doubt, attach this skill. Wrong stage_ids and wrong table names burn
API quota and silently misroute jobs.

## Quick orientation — vocabulary

| Term | What it means in Openbridge |
|------|-----------------------------|
| Subscription / pipeline | One connector instance pulling data for the customer. Has a `subscription_id` and a `product_id`. |
| Storage subscription | The destination warehouse (S3, Redshift, BigQuery, …) attached to subscriptions. |
| Product | A data source connector (e.g. id 50 = Amazon Ads — Sponsored Brands). Has a `worker_name`. |
| Stage / `stage_id` | A specific report or payload within a product. A subscription enables a subset of stages. |
| Payload / table | The landing table for a stage. The Rules API returns the bare name (e.g. `amzn_ads_sb_campaigns`); the **actual destination table name** lives in `destination.tablename` of the schema (e.g. `amzn_ads_sb_campaigns_master` for some destinations, `..._v3` for others). Read the schema, don't hardcode suffixes. |
| Rules document | The schema/contract for a table — fields, types, allowed filters. |
| Healthcheck | An ingestion event row (status, error code, file path, timestamps). |
| Job / history transaction | A scheduled or one-off ingest run for a stage on a date. `history_id` is the transaction. |
| Remote identity | A linked external account (Amazon, Google, …) that subscriptions use for upstream auth. |

## Preconditions — check before any non-trivial workflow

Tool availability on the openbridge MCP is **not** uniform across deployments.
Before any workflow that depends on a specific tool, confirm it's actually
registered. Two facts that have caused failures in production:

- **Always call `get_capabilities()` once per session.** It returns which
  tools are enabled, the env vars they require, and any `disabled_reason`.
  Use it instead of guessing from a tool name list.
- **The query tools (`validate_query`, `execute_query`) are conditionally
  registered.** Server registration is gated on
  `OPENBRIDGE_ENABLE_LLM_VALIDATION=true` AND a sampling key
  (`FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY`). On default installs, both
  are absent — `get_capabilities` returns `enabled: false,
  disabled_reason: "missing_sampling_key"` for them, and calling them via
  `execute()` raises `Unknown tool`. Don't promise the user query execution
  without confirming first.
- **Tool registration is dynamic.** Don't hardcode availability into your
  workflow plan; confirm via `get_capabilities` (or `search()` if you only
  need a name).

Also know — tool-name discovery has a quirk: **`tags()` currently returns a
single `untagged (N tools)` bucket** on this server. The family-grouping is
not yet populated. Use `search(query)` with intent phrases instead; treat
`tags()` as a count check, not a discovery tool.

## Core workflows

For end-to-end recipes (job creation, backfills, healthcheck triage, query
execution, subscription management), read **`references/workflows.md`**. The
following are the high-level entry points.

### 1. Discover a table & (optionally) query it

**Always available:** `search_products` → `list_product_tables` →
`get_table_schema(table_name=…)`. The schema includes
`destination.tablename` — that is the **real** name to use in SQL for the
customer's destination (e.g. `sp_orders_v4`, `amzn_ads_sb_campaigns_master`),
not a hand-built `_master` suffix.

> Two API quirks that have burned users: (1) `search_products` matches on
> bare 1-2 word terms — `"orders"`, `"finance"`, `"inventory"` work;
> `"SP Orders"`, `"Seller Partner orders"`, `"Amazon Ads — Sponsored
> Products"` return `[]`. Try the single most specific noun first.
> (2) `get_table_schema` accepts **only** `table_name`. There is no
> `product_id` parameter; passing one raises a v1 validation error.

**Conditional on capabilities:** `validate_query` → `execute_query`. Run
`get_capabilities()` first; if either is `enabled: false`, **stop at
`get_table_schema` and hand the user the SQL to run in their warehouse
client directly.** Do not fabricate a query path that won't work.

When the query tools are available: validate before executing — the
heuristic guard catches missing `LIMIT` and mutating verbs without spending
LLM budget. Set `OPENBRIDGE_ENABLE_LLM_VALIDATION=true` only when the user
explicitly opts into LLM-assisted SQL review.

### 2. Schedule a historical / backfill job

`get_subscription_by_id` → `get_product_stage_ids(product_id)` →
`create_job(subscription_id, date_start, date_end, stage_ids=[…])`.
**`stage_ids` is required by the MCP schema** — `create_job` calls without
it return a Pydantic validation error, not a wildcard backfill. Use ISO
`YYYY-MM-DD` for `date_start` / `date_end`. (Picking only the stages the
user actually needs is still good practice — partial backfills protect the
upstream rate budget.)

### 3. Triage healthchecks

`get_healthchecks(subscription_id=…, filter_date="YYYY-MM-DD")`. The MCP
tool accepts only `subscription_id` and `filter_date` — **it does not
accept `last_days` or `page`**. For multi-day surveys, iterate over dates
inside `execute()`, or fall back to embed-cli (`health check --last-days N`
is a real flag there). For a single failing row, follow up with
`get_history_by_id(history_id)`. Look at `status`, `err_msg`, `error_code`,
`file_path`, `transaction_id`. To clear/cancel a stuck transaction:
`update_history_status(history_id, status="cancelled")`.

### 4. Inspect & manage subscriptions (pipelines)

`get_subscriptions(status="active")` for the inventory.
`get_subscription_by_id(subscription_id="…")` for one — **the `subscription_id`
parameter is a string here, not int**. `update_subscription("…",
attributes)` or `cancel_subscription("…")` for changes (also string IDs).
`get_storage_subscriptions()` for the storage destinations.

> **Type discrepancy across tools is real.** Some tools take
> `subscription_id` as `str` (the by-id / update / cancel family), others as
> `int` (`get_jobs`, `create_job`). Some take `remote_identity_id` as `str`
> (the remote_identity tools), others as `int` (the Amazon service tools).
> Don't assume uniformity — when in doubt, `await get_schema('<tool_name>')`
> first. The skill mirrors the server's mixed types because they are real;
> the server team is tracking unification.

## Code Mode is the default surface

`CODE_MODE=true` is the recommended deployment. In Code Mode the **only**
tools the client sees are:

- `tags()` — count check (currently returns one `untagged (N tools)` bucket
  on this server; family-grouping not yet populated)
- `search(query)` — find tools by intent — **this is your real discovery
  tool**
- `get_schema(tool_name)` / `get_schemas([…])` — get the input/output JSON
  schemas
- `execute(code)` — run Python in a sandbox that can `await` the individual
  Openbridge tools

If the user's client shows only those four (plus optionally `tags`), they're
in Code Mode and you must build calls inside `execute()`. Do **not** try to
call e.g. `get_jobs()` directly — it isn't exposed. The pattern is:

```python
# Inside execute()
schema = await get_schema("get_jobs")  # only if you need to confirm shape
# Note: is_primary is a STRING ('true'/'false'), not a Python bool
jobs = await get_jobs(subscription_id=987, status="active", is_primary="true")
return jobs
```

Read **`references/code-mode.md`** for sandbox limits, output shape, and the
common pitfalls (no `requests`, 30s timeout, 50MB memory cap).

If `CODE_MODE=false` is set, every tool listed in
**`references/tools-catalog.md`** is exposed by name and you can call them
directly. The startup logs emit a WARNING in this mode — flag it to the user
if you see it and ask whether they want Code Mode back on.

## Authentication

The **production endpoint is `https://mcp.openbridge.com/mcp/`** and it
runs in **OAuth proxy mode** — clients authenticate via a browser-based
OAuth code flow rather than passing raw refresh tokens. There is **no
`Authorization` header to set** in the client config; FastMCP's OAuthProxy
handles the redirect, code exchange, and session-token issuance.

The skill's `mcp-servers.json` reflects this: the URL defaults to the
production endpoint, and the `headers` block is intentionally absent
because OAuth state is managed by the MCP client, not by static headers.
For self-hosted instances pointing at a non-production URL, override
`OPENBRIDGE_MCP_URL`.

`OPENBRIDGE_AUTH_MODE` selects the mode:

- **`oauth_proxy`** (production default at `mcp.openbridge.com`) — OAuth
  code flow. Clients authenticate via browser. The MCP client handles
  token storage and refresh.
- **`refresh_token`** (self-hosted / local-dev convenience) — clients
  pass `Authorization: Bearer <refresh_token>` (shape `xxx:yyy`) or an
  unexpired JWT. Server exchanges refresh tokens via the Openbridge auth
  API and caches per-tenant.

For self-hosted multi-tenant deployments in `refresh_token` mode,
**`OPENBRIDGE_REQUIRE_CLIENT_AUTH=true` is required** — without it an
un-authed request silently runs as the server principal (cross-tenant
data leak). Flag this to any user setting up a shared instance. Not
applicable in `oauth_proxy` mode — the OAuthProxy enforces auth at the
transport layer.

The embed-cli fallback (separate from the MCP) uses `REFRESH_TOKEN` as an
env var or a sourced `config.env`. Tokens are sensitive — never echo
them, never paste them into ticket trackers, never bake them into a skill
or script.

## Error envelopes — what to expect on failure

The openbridge MCP failure surface is **mixed today** — there are **three
distinct failure modes**, and any explanation you give the user about an
error response must name all three before identifying which one they're
looking at. This is non-negotiable: a user who only learns about one mode
will misroute recovery on the other two.

**Mode 1 — v1 envelope (returned)**: rate limit, validation pre-flight
rejection, internal server exception. Returns a dict with `error_kind`,
`tool`, `summary`, `_envelope_version: 1`, etc. Pattern-match
`error_kind` for recovery.

**Mode 2 — flat error dict (returned)**: not-found on
`get_remote_identity_by_id` (`{"error": "..."}`), upstream non-2xx on
Amazon service tools (`{"error", "status", "details"}`), business-logic
errors on individual tools. **No `error_kind`, no `_envelope_version`.**
Treat as a tool-specific terminal error; don't pattern-match envelope
fields.

**Mode 3 — raises out of `execute()`**: unknown tool name (typo or
capability disabled), wrong keyword argument, wrong type. The exception
propagates out of the sandbox and `try/except` inside `execute()` cannot
reliably catch it. Recovery: validate inputs via `get_schema` *before*
the call, not after.

When the user shows you any error response from the MCP, the response
template is: (1) name all three modes, (2) identify which mode the
response is in, (3) prescribe the appropriate recovery for that mode.
The full table including the raise paths and the recovery patterns is in
**`references/error-envelope.md`**.

When you do get a v1 envelope:

```json
{
  "error_kind": "mcp_input_validation",
  "tool": "get_jobs",
  "summary": "subscription_id must be an integer",
  "details": [{"path": "subscription_id", "issue": "type", "received_type": "string"}],
  "hints": ["Pass subscription_id as an int, e.g. 128853"],
  "error_code": "INPUT_VALIDATION_FAILED",
  "retryable": false,
  "_envelope_version": 1
}
```

When you see one, **check `error_kind` first** — it routes recovery logic.
For `rate_limited`, look at `_meta.retry_after_seconds` and back off rather
than retrying immediately. For `auth_error`, walk the user through token
refresh. For full taxonomy and `_meta.*` fields read
**`references/error-envelope.md`**.

## Embed-cli fallback in one paragraph

When the MCP isn't attached, the same workflows run via Docker:

```bash
docker run --rm -e "REFRESH_TOKEN=$OPENBRIDGE_REFRESH_TOKEN" \
  openbridge/embed-cli jobs list --subscription 128853
```

Common subcommands: `jobs list|create|batch`, `subscription list|update`,
`health check`, `stages list --product`, `identity list|get`, `user info`.
For batch backfills the CSV needs `date,subscription_id` (and optionally
`stage_id`). The full reference is **`references/embed-cli.md`**.

## Safety guards

- **Never** call `execute_query` without a `LIMIT` clause or
  `allow_unbounded=True`. The heuristic validator will block you anyway, but
  flag it before retrying with the override.
- **`stage_ids` is required by the MCP `create_job` schema** — omission
  returns a Pydantic validation error, not a wildcard backfill. (On
  embed-cli, `--stage` is optional and omission *does* trigger a wildcard,
  which burns upstream rate budget — prefer a CSV with a `stage_id` column
  there.)
- **Never** echo refresh tokens or JWTs back to the user, into logs, or into
  files. Pull them from env vars only.
- **Always** cite the `subscription_id`, `history_id`, and `error_code` when
  reporting on a failed run — Openbridge support cannot triage without them.

## Resources

| Path | When to read |
|------|--------------|
| `mcp-servers.json` | Always — declares the MCP server URL and required tools |
| `references/code-mode.md` | Before writing any `execute()` Python block |
| `references/workflows.md` | When running a real workflow (jobs, queries, healthchecks, subscriptions) |
| `references/tools-catalog.md` | When `CODE_MODE=false` or the user asks for the full direct tool list |
| `references/error-envelope.md` | When parsing or interpreting an MCP error response |
| `references/embed-cli.md` | When running embed-cli instead of the MCP |
| `evals/evals.json` | Test cases — extend when adding behavior |
