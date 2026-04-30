# MCP Error Envelope Contract — v1

**Status:** v1 (draft, pre-Ads-port)
**Scope:** Cross-server contract for error envelopes and pre-flight normalization telemetry across `amazon_sp_mcp` and `amazon_ads_mcp`. Extensible to other Openbridge MCP servers.
**Authority:** This document is the source of truth for envelope *shape*. Each server provides its own *implementation*. Conformance is enforced via the JSON Schema in `schemas/error-envelope.schema.json` and the test suite in `tests/conformance/`.

## Purpose

Agents calling multiple Openbridge MCP servers should be able to write a single error handler that works against all of them. To make that possible, every server emits errors in the same envelope shape with a shared `error_kind` taxonomy. Implementations differ; the wire format does not.

## Design principles

1. **Shapes shared, code per-server.** No shared Python package. Each server implements the contract in its own module using its own dependencies. Coordinated dependency releases would fight natural drift between SP-API and Ads API.
2. **Additive, not replacing.** New optional fields (`_meta.*`) layer on top without breaking consumers that ignore unknown keys. Required fields change only with a `_envelope_version` bump and a one-release `legacy_error_kind` migration window.
3. **Schema-driven where possible, hint-driven as fallback.** Pre-flight argument normalization corrects canonical-key mismatches before they reach upstream. Hints fire on errors that pre-flight cannot prevent (enum values, server-side rules).
4. **Telemetry surfaces server-only knowledge.** Rate-limit headers, schema rewrites, and upstream warnings are visible to the server but invisible to the agent unless the server emits them. The contract specifies which surfaces and how.

## Versioning

Every emitted envelope includes `_envelope_version` (integer). Server capabilities expose the same value as `contract_version`. Bumping the version requires:

- A `legacy_error_kind` migration window of one release for breaking taxonomy changes
- A `CHANGELOG.md` entry in each affected server with cutover date
- Updated conformance fixtures in this repo

| Version | Status | Adds |
|---|---|---|
| 1 | Current | Envelope core + `_meta.normalized` + `error_kind` v1 taxonomy + hint categories |
| 1.1 (planned) | Phase 3 | `rate_limit` + `retry_after_seconds` on `rate_limited` errors; `_meta.rate_limit` on success |
| 1.2 (planned) | Phase 4 | `_meta.warnings[]` on success path; SP `auth_error` split |

## Required envelope fields (v1)

Every error returned by a conforming server is a JSON object with these required fields:

| Field | Type | Description |
|---|---|---|
| `error_kind` | string | One of the enum values defined below. Determines which downstream handler runs. |
| `tool` | string | The MCP tool name that failed. `"unknown_tool"` if unknowable. |
| `summary` | string | One-line human-readable summary. Stable enough to log but not stable enough to string-match. |
| `details` | array of objects | Structured per-issue records. Each entry has `path` (string, dotted), `issue` (string), `received_type` (string). |
| `hints` | array of strings | Actionable next steps. Bounded length (server may truncate). |
| `examples` | array | Optional examples of valid input. May be empty. |
| `error_code` | string | Stable machine-readable code drawn from the standardized vocabulary in the [Standardized `error_code` vocabulary](#standardized-error_code-vocabulary) section. Same condition produces the same code on both servers. |
| `retryable` | boolean | Whether retrying with the same input could succeed. |
| `_envelope_version` | integer | Currently `1`. Required. |

Optional during migration windows:

| Field | Type | Description |
|---|---|---|
| `legacy_error_kind` | string | Prior `error_kind` value during a one-release migration. Dropped in the release after the version bump. |

## Optional `_meta` fields

`_meta` carries telemetry that is additive and may be absent. Consumers must tolerate `_meta` being missing entirely.

### `_meta.normalized` (v1, shipped on SP)

Array of pre-flight normalization events. Emitted using **attempted_normalization** semantics — the array is populated when the middleware *attempted* to normalize, including cases where no mutation occurred (i.e., `unknown_field_passed_through`).

```json
{
  "_meta": {
    "normalized": [
      {"kind": "renamed", "from": "MarketplaceIds", "to": "marketplaceIds", "reason": "schema_canonical_key"}
    ]
  }
}
```

`kind` values (closed enum, v1):

| Value | Meaning |
|---|---|
| `renamed` | Non-canonical key matched a unique schema field; rewritten in place. `from`/`to` required. |
| `dropped_alias` | Both canonical and alias provided; alias dropped to satisfy strict schema. `from` and `canonical` required. |
| `coerced` | Type adjusted to match schema (initially: scalar → single-item array). `field`, `from_type`, `to_type` required. |
| `unknown_field_passed_through` | Field not in schema; passed to upstream as-is. Emitted only when `MCP_STRICT_UNKNOWN_FIELDS=false`. `field` required. |
| `unknown_field_rejected` | Field not in schema; will be rejected downstream by `SchemaValidationMiddleware`. Emitted when `MCP_STRICT_UNKNOWN_FIELDS=true` (default). Round 12 follow-up to keep the event label aligned with actual outcome. `field` required. |

Reserved (not emitted in v1, may be emitted in future versions): `unknown_field_dropped`.

`_meta.normalized` is emitted on both **successful responses** (when at least one event occurred) and **error envelopes** (when normalization happened before the failing call). On clean calls with no normalization, the field is absent.

Telemetry emission is gated by `MCP_SCHEMA_KEY_NORMALIZATION_META` (currently default off; see Phase 5 in implementation plan).

### `_meta.rate_limit` (v1.1, shipped on both error and success paths)

Object describing upstream rate-limit headroom at the time of the response. Emitted **only when** at least one of the three values is parseable from upstream headers. Absent otherwise — servers must not emit synthetic or null values.

```json
{
  "_meta": {
    "rate_limit": {
      "limit_per_second": 0.0167,
      "remaining": 0,
      "reset_at": "2026-04-25T17:42:33Z"
    }
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `limit_per_second` | number or string | Numeric when parseable; raw string when upstream returns a non-numeric value. |
| `remaining` | number or string | Same. |
| `reset_at` | string | Raw upstream value. ISO-8601 preferred; servers pass through whatever upstream returns. |

Phase 3 status: shipped on both servers, both paths.

- **SP** emits on errors via `error_envelope.py:_build_envelope` and on success via `server/meta_injection_middleware.py:MetaInjectionMiddleware`. Header parsing in `utils/http_client.py:extract_response_meta` (`x-amzn-ratelimit-*`).
- **Ads** emits on errors via `middleware/error_envelope.py:_merge_http_meta` and on success via `middleware/meta_injection_middleware.py:MetaInjectionMiddleware`. Header parsing in `utils/http/rate_limit_headers.py:extract_rate_limit_meta` (`X-RateLimit-*`).

Both implementations honor the same emission contract: only fields parseable from upstream headers are emitted; absent headers result in absent keys, not synthetic ``None`` values.

### `_meta.retry_after_seconds` (v1, partial — shipped on SP error path)

Number of seconds to wait before retrying. Emitted on `rate_limited` errors and on other 4xx/5xx errors that include a `Retry-After` header.

```json
{
  "_meta": {
    "retry_after_seconds": 1.2
  }
}
```

Phase 3 status: shipped on both servers, both paths, under `_meta.retry_after_seconds`. Field placement is canonical: always under `_meta`, never at root envelope level.

### `_meta.warnings[]` (v1, partial — shipped on SP error path)

Array of warnings about the response itself. Each entry has the same shape as a degraded-but-successful condition: `{kind, summary, details, hints}`.

```json
{
  "_meta": {
    "warnings": [
      {"kind": "upstream_warning", "summary": "199 - 'cached response'", "details": [], "hints": []}
    ]
  }
}
```

Currently shipped: SP emits `_meta.warnings` on error envelopes when upstream `Warning` headers are present (see SP `utils/http_client.py:97-118`). Phase 4 adds emission on success responses for degraded-but-successful conditions and defines per-server `kind` vocabularies.

Per-server `kind` vocabularies are documented in this document's appendices. Both servers emit ``upstream_warning`` automatically for any RFC 7234 ``Warning`` response header. Domain-specific kinds (e.g. ``cached_or_stale_data``, ``profile_scope_warning``) are reserved values that servers may emit when they detect the corresponding condition; agents must tolerate the reserved values appearing on responses.

## `error_kind` taxonomy (v1)

Closed enum. Servers must not emit values outside this list.

| Value | Server | Meaning |
|---|---|---|
| `mcp_input_validation` | both | Server-side validator (Pydantic / FastMCP / JSON Schema) rejected before any upstream call. |
| `tool_not_found` | both | Caller invoked a tool name that isn't registered. Pairs with `error_code: TOOL_NOT_FOUND`. Round 12 additive entry; previously routed under `mcp_input_validation`. |
| `sp_api_http` | SP | Upstream SP-API returned an HTTP 4xx/5xx (excluding 429 → `rate_limited` from Phase 3). |
| `ads_api_http` | Ads | Upstream Ads API returned an HTTP 4xx/5xx (excluding 429 → `rate_limited` from Phase 3). Available after Phase 2. |
| `sp_api_client` | SP | Client-side error (e.g., body coercion, unhandled non-HTTP exception inside the SP client). |
| `ads_api_client` | Ads (reserved) | Client-side equivalent for Ads. Reserved; emit only when needed. |
| `auth_error` | both | Identity/credential problem. Available on Ads from Phase 2; on SP from Phase 4. Until then: SP returns these as `sp_api_http` or `sp_api_client`. |
| `rate_limited` | both | Upstream returned 429 OR pre-flight rate limiter triggered. Available from Phase 3. Until then: SP returns 429s as `sp_api_http`; Ads as `ads_api_http`. |
| `sandbox_runtime` | both (Code Mode only) | The Code Mode sandbox hit a known runtime limitation (blocked stdlib, sandbox-specific behavior). |
| `internal_error` | both | Server itself broke. Unhandled exception that wasn't classified above. |

### Migration: `legacy_error_kind`

When a release reclassifies an error from one bucket to another, the envelope includes `legacy_error_kind` carrying the prior value. Cutover:

- **Release N** — adds new `error_kind` value, emits `legacy_error_kind` populated with the prior value.
- **Release N+1** — drops `legacy_error_kind`. Consumers must have migrated.

CHANGELOGs in each server document the cutover date.

## Hint categories (v1)

Hint *categories* are shared (so cross-server agent code can pattern-match the kind of help). Hint *matchers* (rename tables, similarity thresholds, fuzzy logic) are per-server.

| Category | Description | Example |
|---|---|---|
| `case_mismatch` | Field name has wrong case for the schema. | "Use `marketplaceIds` instead of `MarketplaceIds` for this v2 endpoint." |
| `did_you_mean` | Field or value has a close-but-not-exact match. | "Did you mean `marketplaceIds`? Got `marketplaceId`." |
| `enum_suggest` | Value is not in the closed enum; suggest valid values. | "`CONFIRMED` is not valid for `orderStatuses`. Valid: [...]." |
| `missing_required` | Required field is absent. | "Required: `marketplaceIds`. See schema." |

In v1, hints are emitted as plain strings in the `hints` array. Future versions may add a structured `hints` form with explicit `category` tagging.

## Pre-flight argument normalization

Before FastMCP validation, the server may rewrite client-provided arguments to match the canonical schema. This is the pre-flight equivalent of the post-flight `case_mismatch` hint.

### Behavior contract

- **Unique schema match** → rewrite the key to the canonical form.
- **Ambiguous match** (key matches more than one schema field via case-insensitive comparison) → leave unchanged.
- **No match** → leave unchanged. Pass to upstream as-is. Emit `unknown_field_passed_through` event.
- **Canonical key already present** alongside the alias → keep canonical, drop alias. Emit `dropped_alias` event.
- **Schema field is array-typed but client provided scalar** → wrap as single-item array. Emit `coerced` event.

### Strict-unknown-fields default (Round 12, SP-7)

Both servers honor `MCP_STRICT_UNKNOWN_FIELDS` (default `True`). When
on, the `SchemaValidationMiddleware` injects
`additionalProperties: false` into tool schemas that don't declare
their own intent. Result: typo'd or unknown top-level fields surface
as `mcp_input_validation` envelopes with
`error_code: SCHEMA_ADDITIONAL_PROPERTIES` instead of being silently
passed through to the upstream API.

The middleware respects existing intent:

- Schema declares `additionalProperties: false` → no change (already strict).
- Schema declares `additionalProperties: true` → no override (author opted in).
- Schema declares `additionalProperties: <sub-schema>` → no override
  (extras validated against the sub-schema).
- Schema is silent → middleware injects `additionalProperties: false`.

Set `MCP_STRICT_UNKNOWN_FIELDS=false` as an escape hatch when an
upstream spec ships fields ahead of the packaged OpenAPI and strict
rejection would block valid calls.

### Configuration

| Env var | Default | Effect |
|---|---|---|
| `MCP_SCHEMA_KEY_NORMALIZATION_ENABLED` | `true` | Master switch. Set to `false` to bypass normalization entirely (escape hatch for upstream API changes that lag the OpenAPI spec). |
| `MCP_SCHEMA_KEY_NORMALIZATION_META` | `false` (v1) | Whether to emit `_meta.normalized` events. Default flips to `true` after parametric soak (see implementation plan Phase 5). |

Both servers honor these env var names with identical semantics. No aliases.

## Server capabilities

Each server exposes its supported contract version and `error_kind` enum in its MCP capabilities response so agents can discover them at startup:

```json
{
  "openbridge_envelope": {
    "contract_version": 1,
    "error_kinds": ["mcp_input_validation", "sp_api_http", "sp_api_client", "sandbox_runtime", "internal_error"]
  }
}
```

A server's `error_kinds` list is a subset of the master taxonomy in this document. Agents must tolerate values they do not recognize.

## Conformance

A server conforms to v1 when:

1. Every error response validates against `schemas/error-envelope.schema.json`.
2. Every emitted `error_kind` value appears in the v1 taxonomy table.
3. `_envelope_version: 1` is present on every error envelope.
4. `_meta.normalized` events use only the four v1 `kind` values.
5. Server capabilities include `openbridge_envelope.contract_version: 1` and a valid `error_kinds` subset.
6. The fixtures in `tests/conformance/fixtures/` for that server's specific error classes match the JSON Schema.

The conformance suite in `tests/conformance/` is run by both server repos against their tagged releases. A release that fails conformance does not ship.

## Lockstep release mechanism

Phase 3 and Phase 5 require both servers to release on the same calendar day with cross-referenced `CHANGELOG.md` entries. The conformance suite in this repo is the merge gate — a release does not ship until both servers pass against the same suite version.

## Server-specific appendices

### SP appendix

- **Module:** `amazon_sp_mcp/src/amazon_sp_mcp/server/error_envelope.py`
- **`error_kind` values used:** `mcp_input_validation`, `sp_api_http`, `sp_api_client`, `sandbox_runtime`. After Phase 3: `rate_limited`. After Phase 4: `auth_error`.
- **Header parsing for `_meta.rate_limit`:** `x-amzn-ratelimit-limit`, `x-amzn-ratelimit-remaining`, `x-amzn-ratelimit-reset` (see `utils/http_client.py:69`).
- **`_meta.warnings` kinds:** `upstream_warning` (auto-emitted from RFC 7234 ``Warning`` headers; shipped). Reserved values for domain-specific conditions (emitted when SP detects them): `cached_or_stale_data`, `partial_results`, `marketplace_not_enabled_for_identity`, `deprecated_parameter_accepted`.
- **Hint matchers:** Curated `_PASCAL_CASE_RENAMES` table for `case_mismatch` (see `error_envelope.py:378`). Region alias suggestions for `did_you_mean` (see `_REGION_SUGGESTION_ALIASES`).
- **BEHAVIOR.md:** `amazon_sp_mcp/BEHAVIOR.md`.

### Ads appendix (post-Phase 2)

- **Module:** `amazon_ads_mcp/src/amazon_ads_mcp/middleware/error_envelope.py`
- **`error_kind` values used:** `mcp_input_validation`, `ads_api_http`, `auth_error`, `internal_error`. After Phase 3: `rate_limited`.
- **Header parsing for `_meta.rate_limit`:** `X-RateLimit-*` family (Ads-specific).
- **`_meta.warnings` kinds:** `upstream_warning` (auto-emitted from RFC 7234 ``Warning`` headers; shipped). Reserved values for domain-specific conditions (emitted when Ads detects them): `cached_or_stale_data`, `partial_results`, `profile_scope_warning`, `deprecated_parameter_accepted`.
- **Hint matchers:** Per-server (see Ads `BEHAVIOR.md` post-Phase 2).
- **BEHAVIOR.md:** `amazon_ads_mcp/BEHAVIOR.md` (created in Phase 2).

## Standardized `error_code` vocabulary

`error_code` is a stable machine-readable identifier for the failure
condition. Same condition → same code, regardless of which server emitted
the envelope. Agents can branch on `error_code` for recovery logic
without parsing the human-readable `summary`.

### Cross-server canonical codes

| Code | When emitted | error_kind |
|---|---|---|
| `INPUT_VALIDATION_FAILED` | Pydantic / FastMCP / typed-validation rejected input pre-flight | `mcp_input_validation` |
| `TOOL_NOT_FOUND` | Caller invoked a tool name that isn't registered | `tool_not_found` (Round 12; was `mcp_input_validation` in Round 11) |
| `AUTHENTICATION_ERROR` | Generic auth failure (token, credential, OAuth) | `auth_error` |
| `INTERNAL_ERROR` | Unhandled server-side exception | `internal_error` |
| `TOOL_EXECUTION_FAILED` | Server-side error before reaching upstream API | `*_api_client` (per-server) |
| `BODY_COERCION_INVALID_JSON` | JSON-like string in body could not be parsed | `*_api_client` (per-server) |
| `RATE_LIMITED` | Upstream returned 429 OR pre-flight rate limiter triggered | `rate_limited` |
| `CODE_MODE_SANDBOX_LIMITATION` | Code Mode sandbox hit a known runtime limitation | `sandbox_runtime` |

#### JSON Schema validation codes (Round 11)

When the pre-flight `SchemaValidationMiddleware` rejects a tool call
because the args fail the tool's published JSON Schema, the envelope
carries one of these canonical codes. Both servers MUST emit the same
code for the same shape; the canonical mapping lives at
[`schemas/jsonschema_error_codes.json`](schemas/jsonschema_error_codes.json)
and is enforced by a parity test in
`amazon_ads_mcp/tests/unit/test_schema_validation_middleware.py::TestCanonicalMappingAlignment::test_sp_and_ads_maps_byte_identical`.

| Code | jsonschema validator | Notes |
|---|---|---|
| `SCHEMA_TYPE_MISMATCH` | `type` | `details.expected_type`, `details.received_type` |
| `SCHEMA_REQUIRED` | `required` | `details.field` is the missing field name |
| `SCHEMA_MAX_ITEMS` | `maxItems` | `details.limit`, `details.actual` |
| `SCHEMA_MIN_ITEMS` | `minItems` | `details.limit`, `details.actual` |
| `SCHEMA_MAX_LENGTH` / `SCHEMA_MIN_LENGTH` | `maxLength` / `minLength` | `details.limit` |
| `SCHEMA_MAXIMUM` / `SCHEMA_MINIMUM` | `maximum` / `minimum` | `details.limit` |
| `SCHEMA_ENUM_MISMATCH` | `enum` | `details.allowed[]` |
| `SCHEMA_PATTERN_MISMATCH` | `pattern` | — |
| `SCHEMA_FORMAT_INVALID` | `format` | — |
| `SCHEMA_ADDITIONAL_PROPERTIES` | `additionalProperties` | `details.extra` (offending key) |
| `SCHEMA_ONE_OF_FAILED` | `oneOf` | — |
| `SCHEMA_ANY_OF_FAILED` | `anyOf` | — |
| `SCHEMA_ALL_OF_FAILED` | `allOf` | — |
| `SCHEMA_UNIQUE_ITEMS` | `uniqueItems` | — |
| `SCHEMA_CONST_MISMATCH` | `const` | — |
| `SCHEMA_MULTIPLE_OF` | `multipleOf` | — |
| `SCHEMA_VALIDATION_FAILED` | (any unrecognized validator) | Fallback; original validator name in `_meta` |

**Domain validators** (Round 12) — closed-enum checks against runtime
caches that share the `SCHEMA_*` envelope shape but don't correspond
to a jsonschema validator. They still carry
`error_kind: mcp_input_validation` and the same `details.field` /
`details.hints` surface.

| Code | Server | Tool | Closed-enum source |
|---|---|---|---|
| `SCHEMA_IDENTITY_NOT_FOUND` | SP | `set_active_identity` | `auth_manager.list_identities()` cached results. Empty/erroring cache falls through to the upstream call (best-effort gate). |

`details.field` is rendered as a JSON-pointer-style path resolved from
`jsonschema.ValidationError.absolute_path` — e.g. a bad type two levels
deep surfaces as `filters/0/marketplaceId`, not just `filters`. Top-level
fields use the bare name.

### Per-server upstream HTTP codes

Upstream HTTP errors keep a server-prefixed numeric code so the boundary
is explicit:

| Code pattern | Server | When emitted |
|---|---|---|
| `SP_API_HTTP_<NNN>` | SP | Upstream SP-API returned HTTP N |
| `ADS_API_HTTP_<NNN>` | Ads | Upstream Amazon Ads API returned HTTP N |
| `OPENBRIDGE_HTTP_<NNN>` | SP | Internal Openbridge service (identity, etc.) returned HTTP N |

For 401/403 the `error_kind` is `auth_error` but the `error_code` keeps
the per-server `*_API_HTTP_4NN` form so agents can still map to the
specific failure.

### Reserved future codes

| Code | When emitted | Status |
|---|---|---|
| `OAUTH_ERROR` | OAuth-flow specific failure (state mismatch, code exchange) | Reserved; `auth_error` envelopes today carry `AUTHENTICATION_ERROR` |
| `TOKEN_ERROR` | Refresh/access token failure | Reserved |
| `PERMISSION_DENIED` | Server-side permission rejection (distinct from auth credential failure) | Reserved |

## Behavior choices documented (Round 11)

These are deliberate design decisions called out in client conformance
reports. All are **working as designed**; documented here so future
reports can map "this is intentional" without re-litigating.

### Tool-not-found classification (SP-1)

`fastmcp.exceptions.NotFoundError` (raised when a caller invokes an
unknown tool name) classifies as:

  - `error_kind: tool_not_found` *(Round 12; was `mcp_input_validation`
    in Round 11. The `error_code` and the rest of the envelope are
    unchanged.)*
  - `error_code: TOOL_NOT_FOUND`

Reason: the call never reached the upstream API, so neither
`*_api_client` nor `*_api_http` would be accurate. Tool-name typos are
purely an MCP-side mistake — the dedicated `tool_not_found` kind lets
agents branch on a single value rather than parsing
`error_code: TOOL_NOT_FOUND` out of the broader
`mcp_input_validation` bucket. Clients should branch on `error_kind`
or `error_code`, search the catalog, and retry with the corrected name.

### Identity-validation classification (SP-3)

Typed validators (e.g. `set_active_identity`, `set_region`) raise
`SPValidationError` / `AdsValidationError` with `error_kind:
mcp_input_validation` (not `*_api_client`). Reason: the call never
reaches upstream; the failure is purely schema-shaped. Carries
`error_code: INPUT_VALIDATION_FAILED` for legacy-typed validation;
`SCHEMA_*` for the new pre-flight schema validator.

### `"ToolError: "` prefix in stringified exceptions (SP-2)

The wire payload (raw JSON-RPC `error.message` or `result.content[0].text`)
is a clean v1 envelope JSON. The `"ToolError: "` prefix is added by the
`fastmcp.client` SDK's `ToolError.__str__` rendering on the **client**
side. Phase 0b (Round 11) and Round 12 wire captures both confirmed
the prefix is **not** in the server's response, on either SP or Ads.

Both servers are wire-symmetric. The asymmetry the conformance report
flagged is in the SDK rendering layer, not the server. Recommended
client patterns:

```python
# CORRECT — read the wire payload directly via the SDK's structured
# access path. No prefix stripping required; works on both servers.
envelope = json.loads(exc.error_data.content[0].text)

# WORKS — strip the SDK prefix first if you only have str(exc).
envelope = json.loads(str(exc).removeprefix("ToolError: "))

# WRONG — fails on both SP and Ads because the SDK prepends "ToolError: "
# before the JSON in __str__.
envelope = json.loads(str(exc))
```

This is documented design, not a server bug. Round 12 ships no code
change here; the Round 11 inner-envelope extractor (which strips the
prefix when the server-side translator surfaces an inner envelope from
the Code Mode sandbox bridge) continues to apply on the server side.

### Unknown-field passthrough (SP-7)

When the schema-driven normalization layer can't map an incoming key
to a single canonical field (no schema match, or ambiguous match), it
records `unknown_field_passed_through` in `_meta.normalized[]` and
forwards the field to the tool function unchanged. The tool's own
schema then decides accept/reject (after Round 11, the new
`SchemaValidationMiddleware` typically rejects with
`SCHEMA_ADDITIONAL_PROPERTIES` if the schema sets
`additionalProperties: false`).

Reason: no silent drops; the event in `_meta.normalized[]` is the
auditable artifact. Clients that want strict reject-on-unknown should
ensure their tool schemas use `additionalProperties: false`.

### 401/403 → `auth_error` (SP-8)

Upstream HTTP 401 and 403 classify as:

  - `error_kind: auth_error`
  - `error_code: SP_API_HTTP_4NN` / `ADS_API_HTTP_4NN` (preserves
    boundary distinction)

Reason: 401/403 always means "fix credentials, not retry the same
call". A generic `*_api_http` envelope would lose the
agent-actionable hint surface (`"Re-authorize the active identity if
expired."`). Clients should branch on `error_kind` for credential
recovery flows and on `error_code` for server-specific telemetry.

### Null/non-dict success response (SP-11)

`MetaInjectionMiddleware` only attaches `_meta` to dict responses.
When a tool returns `None`, a list, or a primitive, the response is
forwarded untouched without `_meta`. Reason: protocol stability — the
middleware doesn't reshape return types. Clients that need rate-limit
telemetry on every call should call a dict-returning tool or wrap the
result themselves.

## Adoption status

This document defines v1 of the contract. Adoption by each server is staged and tracked here.

| Server | `_envelope_version` field | `contract_version` capability | Conformance status |
|---|---|---|---|
| `amazon_sp_mcp` | Emitted (Round 5+) | Pending capability surface | v1-compliant; Round 11 adds `TOOL_NOT_FOUND` re-classification and pre-flight `SCHEMA_*` codes via `SchemaValidationMiddleware`. |
| `amazon_ads_mcp` | Emitted (Round 5+) | Pending capability surface | v1-compliant; Round 11 mirror complete — same `TOOL_NOT_FOUND` + `SCHEMA_*` codes via parallel middleware. Cross-server canonical-mapping parity asserted by `test_sp_and_ads_maps_byte_identical`. |

The captured fixtures in `tests/conformance/fixtures/` represent the shape each server **will emit once it adopts v1**, not the current literal output. The conformance suite validates this target shape against the JSON Schema. Each server's adoption PR is a small, focused change (add `_envelope_version: 1` to its envelope builder; expose `contract_version: 1` in its capabilities response) and lands separately under each server team's authorization.

## See also

- `schemas/error-envelope.schema.json` — JSON Schema for machine validation.
- `tests/conformance/` — fixture-based conformance test suite.
- `amazon_sp_mcp/BEHAVIOR.md` — SP-specific behavior contract.
- `amazon_ads_mcp/BEHAVIOR.md` — Ads-specific behavior contract (post-Phase 2).
