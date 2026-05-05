# MCP error envelope (v1)

Every error the Openbridge MCP returns conforms to the cross-server v1
envelope contract. The shape is stable; the values vary by failure mode.
Knowing the envelope lets you write recovery logic that works against any
Openbridge MCP (this one, the Amazon SP MCP, the Amazon Ads MCP) without
string-matching summaries.

The full contract lives at `openbridge-mcp/CONTRACT.md` in the server repo.
This page is the working subset for skill use.

## Shape

```json
{
  "error_kind": "rate_limited",
  "tool": "get_jobs",
  "summary": "Upstream returned 429 — back off.",
  "details": [
    {"path": "subscription_id", "issue": "n/a", "received_type": "int"}
  ],
  "hints": ["Wait 1.2 seconds and retry."],
  "examples": [],
  "error_code": "RATE_LIMITED",
  "retryable": true,
  "_envelope_version": 1,
  "_meta": {
    "rate_limit": {
      "limit_per_second": 0.0167,
      "remaining": 0,
      "reset_at": "2026-04-25T17:42:33Z"
    },
    "retry_after_seconds": 1.2
  }
}
```

Required fields: `error_kind`, `tool`, `summary`, `details`, `hints`,
`examples`, `error_code`, `retryable`, `_envelope_version`. `_meta` is
optional — tolerate it being absent.

## `error_kind` taxonomy (v1)

Closed enum. Branch your recovery logic on this.

| Value | Meaning | Recovery |
|---|---|---|
| `mcp_input_validation` | Server-side validator rejected the input pre-flight | Read `details[]` paths, fix the call, retry. Not retryable as-is. |
| `tool_not_found` | Tool name isn't registered (typo, Code Mode mismatch) | Call `tags()` / `search()` to find the right name. |
| `auth_error` | Token / credential / OAuth problem | Refresh the token. If `OPENBRIDGE_REFRESH_TOKEN` is missing, prompt the user to set it. |
| `rate_limited` | Upstream 429 OR pre-flight rate limiter triggered | Look at `_meta.retry_after_seconds`; back off, then retry. |
| `sandbox_runtime` | Code Mode sandbox limitation hit (blocked stdlib, timeout, OOM) | Restructure the `execute()` block — page the work, drop unsupported imports. |
| `internal_error` | Server-side unhandled exception | Capture and report; this is a server bug. Not safe to retry blind. |

Server-specific kinds (Openbridge-only):

| Value | Meaning |
|---|---|
| `sp_api_http` / `ads_api_http` | Upstream API returned a non-429 4xx/5xx (Openbridge calls into SP / Ads in a few flows) |
| `sp_api_client` / `ads_api_client` | Client-side error in the upstream call (body coercion, unhandled exception inside the upstream client) |

If you see an `error_kind` value that isn't in this list, treat it as
`internal_error` semantics — report and don't retry.

## `error_code` (machine-readable)

Stable per condition, regardless of which Openbridge MCP emits the
envelope. Common codes:

| Code | When |
|---|---|
| `INPUT_VALIDATION_FAILED` | Pydantic / FastMCP / typed-validation rejected pre-flight |
| `TOOL_NOT_FOUND` | Caller invoked a tool name that isn't registered |
| `AUTHENTICATION_ERROR` | Generic auth failure |
| `RATE_LIMITED` | Upstream 429 or pre-flight rate limiter |
| `INTERNAL_ERROR` | Unhandled server exception |
| `TOOL_EXECUTION_FAILED` | Server-side error before reaching upstream API |
| `BODY_COERCION_INVALID_JSON` | JSON-like body string couldn't parse |
| `SCHEMA_ADDITIONAL_PROPERTIES` | Unknown top-level field rejected by strict-unknown-fields gate |

## `_meta` fields you should look at

### `_meta.rate_limit` (success and error path)

When upstream returned a parseable rate-limit header. Absent otherwise —
don't expect this on every response. Use it to pace large list operations
without slamming the upstream.

### `_meta.retry_after_seconds` (error path)

On `rate_limited` and on other 4xx/5xx errors that include a `Retry-After`
header. The number is canonical — never duplicated at the envelope root.

### `_meta.warnings[]` (success and error path)

Array of degraded-but-successful warnings.
`{kind, summary, details, hints}` per entry. `kind: upstream_warning` is
auto-emitted from RFC 7234 `Warning` headers. Other kinds are reserved
(e.g. `cached_or_stale_data`, `partial_results`) and may appear depending
on the underlying API.

### `_meta.normalized[]` (gated by env var, off by default in v1)

Pre-flight argument normalization events. Only emitted when
`MCP_SCHEMA_KEY_NORMALIZATION_META=true`. Tells you which keys were
renamed / coerced before the call ran. Useful for debugging wrong-case
field names against strict schemas.

## Hints

`hints[]` is a bounded list of plain strings. Categories (informal — v1
doesn't structure them):

- `case_mismatch` — wrong case for a schema field (`Use marketplaceIds, not
  MarketplaceIds`)
- `did_you_mean` — close-but-not-exact match (`Did you mean
  marketplaceIds? Got marketplaceId.`)
- `enum_suggest` — invalid enum value, with valid set
- `missing_required` — required field absent

Treat hints as advisory — don't depend on exact wording.

## Migration: `legacy_error_kind`

When a release reclassifies an error from one bucket to another, the
envelope includes `legacy_error_kind` carrying the prior value for one
release. Drop it after the migration window — log it during the cutover so
you can confirm consumers handled the new value.

## Failure modes — return vs raise

The openbridge MCP failure surface is **mixed today**. Not every failure
returns a v1 envelope; some return flat error dicts; some raise out of
`execute()` and cannot be caught reliably. Before pattern-matching
`error_kind`, check the **shape** of what you got back.

| Failure mode | Server behavior today |
|---|---|
| Unknown tool name (typo, capability disabled) | **Raises** out of `execute()`. `try/except` inside the sandbox cannot reliably catch this. |
| Wrong keyword argument | **Raises** (`ValueError` / Pydantic). Same caveat. |
| Wrong type (e.g. `int` where `str` required) | **Raises** `pydantic.ValidationError`. Same caveat. |
| Not-found (most tools) | **Returns** `null` or `[]` — not an error at all. |
| Not-found (`get_remote_identity_by_id`) | **Returns** flat `{"error": "..."}` — no `error_kind`, no `_envelope_version`. |
| Upstream non-2xx (Amazon service tools, query tools) | **Returns** flat `{"error", "status", "details"}` — no envelope. |
| Rate limit (when triggered) | **Returns** v1 envelope per CONTRACT.md — `error_kind: rate_limited` with `_meta.retry_after_seconds`. |
| Validation pre-flight rejection | **Returns** v1 envelope — `error_kind: mcp_input_validation`. |
| Internal server exception | **Returns** v1 envelope — `error_kind: internal_error`. |

### Sandbox cannot reliably catch raise paths

`try/except` around `await tool(...)` *may* catch `pydantic.ValidationError`
or `ValueError`, but the sandbox's exception propagation policy is not
stable across versions. **The safest pattern is to validate inputs *before*
the call** — `await get_schema('<tool_name>')` for any tool you haven't
called this session, and confirm parameter names and types from the schema
rather than guessing. For known-conditional tools (`validate_query`,
`execute_query`), check `get_capabilities()` first; never blindly call them.

### Distinguishing flat error dicts from v1 envelopes

A v1 envelope has all of: `error_kind`, `tool`, `summary`, `details`,
`hints`, `error_code`, `retryable`, `_envelope_version`. A flat error dict
typically has only `error` (and sometimes `status`, `details`). When you
get back a flat dict, **don't pattern-match `error_kind`** — it isn't
there. Treat it as a terminal business error specific to that tool's
contract. The status-code field (when present) tells you whether retry
makes sense; "not found" is permanent.

## Recovery patterns

> These patterns assume the response **is** an envelope dict. Per the
> table above, only some failure modes produce envelopes today. Confirm
> the shape with `isinstance(err, dict) and "error_kind" in err` before
> branching on `error_kind`. For raise-mode failures, the recovery is
> *don't make the call*: validate the input shape via `get_schema` first.

### Rate-limit back-off

```python
err = await create_job(...)  # may return an envelope
if isinstance(err, dict) and err.get("error_kind") == "rate_limited":
    delay = err.get("_meta", {}).get("retry_after_seconds", 1.0)
    # Don't sleep inside execute() — return delay to the caller and let it retry
    return {"retry_after": delay, "envelope": err}
```

### Auth refresh

```python
err = await get_jobs(subscription_id=987)
if isinstance(err, dict) and err.get("error_kind") == "auth_error":
    # The MCP can't refresh the token without a valid refresh token — surface to user
    return {"action_required": "refresh_token", "envelope": err}
```

### Validation failures

```python
err = await create_job(...)
if isinstance(err, dict) and err.get("error_kind") == "mcp_input_validation":
    # Read details[] for the failing paths, correct, retry
    bad_paths = [d["path"] for d in err.get("details", [])]
    return {"correct_these": bad_paths, "envelope": err}
```

## Conformance check

A response conforms to v1 when:

1. Every required field is present
2. `error_kind` is in the v1 taxonomy
3. `_envelope_version: 1` is present
4. `_meta.normalized[]` only uses the four v1 `kind` values
5. Server capabilities expose `openbridge_envelope.contract_version: 1`

Get the live capabilities with `get_capabilities()` and confirm
`openbridge_envelope.contract_version` matches your expectation.
