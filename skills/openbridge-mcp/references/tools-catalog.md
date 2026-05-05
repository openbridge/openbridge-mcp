# Direct tool catalog (`CODE_MODE=false`)

When the deployment runs `CODE_MODE=false`, every Openbridge MCP tool is
exposed by name. (The startup logs emit a WARNING — Code Mode is the
recommended surface; flag this to the user.) This page lists every tool
the MCP exposes, grouped by family, with the **server-side parameter
types verbatim** and the failure shape.

> **Type discrepancies are real on this server.** Some IDs are `str`,
> others `int`, depending on the tool — `subscription_id` is `int` on
> `get_jobs` / `create_job` but `str` on `get_subscription_by_id` /
> `update_subscription` / `cancel_subscription`; `remote_identity_id` is
> `str` on `get_remote_identity*` but `int` on the Amazon service tools.
> The skill mirrors the server; **do not normalize**. Authoritative source
> for any tool: `get_schema(tool_name)` against the live server. The MCP
> server team is tracking unification — see CONTRACT.md for status.

For full input/output JSON schemas, call `get_schema(tool_name)` against
the live server — the schemas drift faster than this doc.

## Capabilities

### `get_capabilities()`

No arguments. Returns currently enabled tools, required env vars, and the
set of opt-in features (e.g. AI query validation, OAuth proxy mode).
**Always call this once per session** before assuming a tool is available
— some tools (the SQL query family) are conditionally registered.

## Jobs

### `get_jobs(subscription_id: int, status: Optional[str] = 'active', is_primary: Optional[str] = 'true')`

Lists jobs for a subscription. **`is_primary` is a string** (`'true'` /
`'false'`), not a Python bool — and the parameter is `is_primary`, not
`primary`. `subscription_id` is `int`. Returns a list.

### `get_job_by_id(job_id: int)`

Single-job fetch. Returns `None` on miss.

### `get_history_by_id(history_id: int)`

Single transaction fetch (one stage × date × subscription run). Returns
the full row including `err_msg`, `error_code`, `file_path`,
`transaction_id`.

### `update_history_status(history_id: int, status: str)`

Mutates the transaction status. Used to cancel stuck `UNPROCESSED` rows.
**Destructive — confirm with the user before calling.**

### `create_job(subscription_id: int, date_start: str, date_end: str, stage_ids: List[int])`

Schedules historical jobs. **All four parameters are required** — the
server schema does *not* allow omitting `stage_ids`; calls without it
return a `pydantic.ValidationError` (raised, not enveloped). Dates are
ISO `YYYY-MM-DD`.

## Healthchecks

### `get_healthchecks(subscription_id: Optional[str] = None, filter_date: Optional[str] = None)`

Lists ingestion events. **The MCP tool accepts only these two
parameters** — there is **no `last_days`, no `page`**. Internal
pagination is hardcoded at 10 pages. `subscription_id` is `str` here.
For multi-day windows, iterate `filter_date` inside `execute()`, or use
the embed-cli (`health check --last-days N` is real on the CLI).

## Subscriptions

### `get_subscriptions(status: str = 'active')`

Paginated, capped at 10 pages internally. **Fail-fast on errors:**
returns `[]` and error-logs rather than partial results, because callers
use the list as a complete inventory. (Behavior verified against server
source; runtime path not independently triggered by this skill team.)

### `get_subscription_by_id(subscription_id: str)`

Single subscription. **`subscription_id` is `str`** here. Returns `None`
on miss.

### `create_subscription(attributes: Dict[str, Any])`

JSON:API attributes payload. Required fields vary by product — call
`get_schema('create_subscription')` first.

### `update_subscription(subscription_id: str, attributes: Dict[str, Any])`

Partial update via JSON:API attributes. **`subscription_id` is `str`**.
Only pass fields to change.

### `cancel_subscription(subscription_id: str)`

Sugar for `update_subscription('<id>', {"status": "cancelled"})`.
**`subscription_id` is `str`**. **Destructive.**

### `get_storage_subscriptions()`

Lists storage destinations. Best-effort: skips individual storages that
fail and returns the rest.

## Products & tables

### `search_products(query: str)`

Case-insensitive substring search over the product `name` field. **Not
fuzzy, not tokenized** — multi-word product names and full phrases
typically return `[]`. Use bare 1-2 word terms:

| Works | Doesn't work |
|---|---|
| `"orders"` | `"SP Orders"`, `"Seller Partner orders"`, `"Amazon orders"` |
| `"finance"` | `"SP Finance reports"` |
| `"inventory"` | `"FBA Inventory Management"` |
| `"Amazon Advertising"` | `"Amazon Ads — Sponsored Products"` (em-dash kills match) |
| `"amzn"` | `"Amazon Advertising Sponsored Products Display Ads API v3"` |

Returns `[{"id", "name", "worker_name"}, ...]`. When in doubt, try the
**single most specific noun** before adding qualifiers.

### `list_product_tables(product_id: int, subscription_id: Optional[int] = None)`

Lists payloads (tables) for a product. **`subscription_id` is `int`** on
this tool. With `subscription_id`, filters to stages enabled for that
subscription.

### `get_product_stage_ids(product_id: Optional[str] = None)`

Stage detail for a product (id, name, schedule). **`product_id` is
`str`** here. Feed `stage_id` values into `create_job` (where they're
`int`).

## Rules / table schemas

### `get_table_schema(table_name: str)`

Rules document for a table. **Accepts only `table_name`** — there is
**no `product_id` parameter** even though `list_product_tables` returned
both. Passing `product_id` raises a v1 validation error. Pass the bare
table name (the `name` field from `list_product_tables`); the `_master`
suffix is auto-stripped on lookup if you include it.

**Read `destination.tablename` from the response** — that is the actual
table name in the customer's destination. Suffix conventions vary:
`_master` (Redshift), `_v3` / `_v4` (per-product versioned), or the bare
name. Don't hardcode; quote what the schema returns.

Returns `None` on ambiguous match (refine the name) or miss.

### `get_suggested_table_names(query: str)`

Fuzzy table-name search via the Rules API. Returns names with `_master`
suffix appended (which may not be the destination's actual name — read
the schema to confirm).

## Query (CONDITIONALLY REGISTERED)

**Both tools are gated** on `OPENBRIDGE_ENABLE_LLM_VALIDATION=true` AND a
sampling key (`FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY`). On default
deployments, **neither tool is registered** — calls raise `Unknown tool`.
Confirm via `get_capabilities()` first; fall back to schema-only output if
disabled.

LLM-assisted review is opt-in only via
`OPENBRIDGE_ENABLE_LLM_VALIDATION=true`; without it, validation is
heuristic-only and SQL never leaves your environment.

### `validate_query(query: str, key_name: str, allow_unbounded: bool = False)`

Heuristic + optional LLM validation. Returns a decision object:
`{decision: {allowed: bool, limit_ok: bool, …}, heuristics: {…},
sampling: {supported, error?}}`.

### `execute_query(query: str, key_name: str, allow_unbounded: bool = False)`

Validates then executes. Returns `[{row}, ...]` on success, or a
single-element error list on failure. **Pass `allow_unbounded=True` only
when the user explicitly accepts the unbounded result.**

## Remote identities

### `get_remote_identities(remote_identity_type_id: Optional[str] = None)`

Paginated list of linked external accounts. **`remote_identity_type_id`
is `str`** here. Partial-results contract: returns rows collected so far
on pagination errors.

### `get_remote_identity_by_id(remote_identity_id: str)`

Single identity, with `attributes` flattened to top-level keys.
**`remote_identity_id` is `str`** here. Returns flat `{"error": "...not
found"}` on miss (NOT a v1 envelope — see `error-envelope.md`).

## Service (Amazon Ads)

### `get_amazon_api_access_token(remote_identity_id: int)`

Exchanges a remote identity for an Amazon Advertising API access token.
**`remote_identity_id` is `int`** here — yes, different type from the
remote-identity tools above. Returns `{"access_token", "client_id"}` on
success or flat `{"error", "status", "details"}` on failure (not an
envelope). **Check for `error` key, not just `access_token`.**

### `get_amazon_advertising_profiles(remote_identity_id: int)`

Lists Amazon Ads profiles for an identity. **`remote_identity_id` is
`int`**. Returns `[]` on any upstream failure (token, region, profile
API).

## Reading the failure shape

Every tool returns either:

- A normal value (list, dict, scalar) on success
- A flat dict with an `error` key on partial / business-logic failures
  (no `error_kind`, no `_envelope_version`)
- An MCP error envelope on hard failures (validation, auth, rate limit,
  internal)
- **Or it raises** — for unknown tool names, wrong keyword args, wrong
  types. Inside `execute()`, raises propagate out of the sandbox; you
  cannot reliably catch them with `try/except`.

For the full return-vs-raise table and recovery patterns, see
**`error-envelope.md`**.
