# Changelog

## 0.3.0 - 2026-05-01

- Breaking: table discovery responses are now normalized objects across `get_suggested_table_names`, `list_product_tables`, and `get_table_schema`. The old list-only discovery shape has been removed.
- Added shared resolver logic in `src/utils/table_resolver.py` to enforce one canonical alias model (Rules path leaf is canonical), deterministic alias expansion (`bare`/`_master`/`_vNN`), and typo suggestion ranking (max 5 suggestions, minimum similarity 0.6).
- `get_suggested_table_names` now returns structured candidates on success and returns a v1 `TABLE_NOT_FOUND` envelope on empty/no-match instead of `[]`.
- `list_product_tables` now returns `{product_id, tables}` and merges payload-backed tables with rules-only discoverables. Rules-only entries omit unavailable payload keys (`id`, `stage_id`) by design.
- `get_table_schema` now resolves alias variants to a canonical lookup key and returns normalized schema metadata (`lookup_key`, `aliases`, `rules_path`, `destination_table`, `schema`); not-found paths now include envelope hints/examples for recovery.
- Added fixture-backed resolver tests under `tests/fixtures/resolver/` and expanded service/products tests to validate the new discovery contract end-to-end.

## 0.2.5 - 2026-04-30

- OAuth UX: skip the local FastMCP consent page (`mcp.openbridge.com/consent?txn_id=...`) by setting `require_authorization_consent="external"` on the OAuthProxy. Auth0 already prompts the user during the upstream `/authorize` step, so the local consent interstitial was a redundant second prompt and the source of the orphan-tab quirk after Allow Access. One fewer hop in the OAuth redirect chain. Auth0's own consent screen still has the same post-Allow dynamics — that's a FastMCP/wire-protocol issue, not solvable from our side without subclassing private OAuthProxy methods.
- Added `scripts/spike-auth0-provider.py` and `scripts/probe-auth0-spike.sh` for future pressure-testing of a direct `Auth0Provider` migration. Confirmed (a) the OAuth metadata + DCR surfaces are equivalent between OAuthProxy and Auth0Provider, (b) Auth0Provider exposes a token revocation endpoint OAuthProxy doesn't, and (c) Auth0Provider would drop the per-request introspection round-trip. Migration deferred — the consent fix above doesn't require it.

## 0.2.4 - 2026-04-30

- Close the last contract self-consistency gap: unknown-tool lookups inside the sandbox now return a `tool_not_found` envelope instead of raising `NotFoundError`/`Exception: Unknown tool: <name>`. The fix lives in the `_EnvelopeUnwrappingCodeMode.call_tool` shim — when `transform._find_tool` returns `None`, we build the v1 envelope (with hints pointing at `get_capabilities` and `not_installed[]`) and return it directly. `openbridge_envelope.error_kinds` is now fully self-consistent: every kind the server declares is actually emitted on a real path.
- Cosmetic fix: `mcp_input_validation` envelope details now report the actual input type via `type(raw_input).__name__` instead of the Pydantic validator name. Passing `"118666"` (string) to an int-typed field now reports `received_type: "str"` — what envelope readers expect.
- Added `TestExecuteBoundary` integration test that drives `_make_execute_tool` with a stub sandbox provider, plugging the unknown-tool unit-test gap that v0.2.3 deferred to live re-test.

## 0.2.3 - 2026-04-30

- Fix #11: sandbox `call_tool()` now returns v1 error envelopes as dict values instead of raising `ToolError(json.dumps(envelope))`. The documented recovery pattern in `CONTRACT.md` —
  ```python
  err = await call_tool(...)
  if isinstance(err, dict) and err.get("error_kind") == "rate_limited":
      ...
  ```
  — now works as written inside Code Mode. Implementation: thin `_EnvelopeUnwrappingCodeMode` subclass of FastMCP's `CodeMode` (`src/server/code_mode.py`) that wraps the inner `call_tool` shim at the sandbox `external_functions` boundary. Non-envelope `ToolError` (plain message, wrong version, malformed JSON) and all non-`ToolError` exceptions (`RuntimeError`, `asyncio.CancelledError`, etc.) propagate unchanged.
- `CONTRACT.md` "Receiving envelopes" rewritten to reflect the surface asymmetry: sandbox callers always receive envelopes as return values; direct MCP-transport callers still see them as `ToolError` raises (wire-protocol constraint — `ToolResult` has no `isError` field). Single-handler `call_with_envelope` recipe retained for direct-transport callers.

## 0.2.2 - 2026-04-30

- Document the two envelope arrival paths (returned vs. raised) and the canonical client recovery recipe in `CONTRACT.md`. The sandbox-raise path is a FastMCP transport constraint (`ToolResult` has no `isError` field); the contract now spells out the catch + `json.loads` recipe and the `call_with_envelope` shortcut so client code can handle both paths uniformly. No code change — `ErrorEnvelopeMiddleware` already conformed; the spec was the gap.

## 0.2.1 - 2026-04-30

- Fix `get_capabilities.summary` arithmetic: added `not_installed_tools` so the invariant `total_tools_declared == enabled_tools + disabled_tools + not_installed_tools` now holds. (Surfaced by skill pressure-test re-run; previously `total=24, enabled=22, disabled=0` was off by 2.)

### Known issue (deferred to follow-up)

- Sandbox-raise paths (unknown tool, Pydantic input validation) still ship the v1 envelope inside `ToolError(json.dumps(envelope))` rather than returning it as a structured result. Recovery code that does `err = await call_tool(...); err.get("error_kind")` still doesn't work on these paths. FastMCP's `ToolResult` has no `isError` flag, so a clean fix needs either a structured payload on `ToolError` or a documented exception-catching recipe — see issue follow-up.

## 0.2.0 - 2026-04-30

- Breaking: normalized numeric tool IDs to strict integers (`StrictInt`) across jobs, subscriptions, healthchecks, products, and remote identity tools.
- Breaking: `get_jobs.is_primary` is now `bool` (`True`/`False`) instead of string (`"true"`/`"false"`).
- Added `get_healthchecks(last_days, page)` contract support, including mutual-exclusion validation for `filter_date` and `last_days`.
- Added v1 error envelope helpers and rolled tool error paths onto `_envelope_version: 1` responses.
- Added FastMCP error-envelope middleware for uncaught exceptions (`tool_not_found`, validation, internal errors).
- Capabilities now report only registered tools, expose `not_installed`, and include `openbridge_envelope.contract_version`.
- Added `OPENBRIDGE_ENABLE_QUERY_EXECUTION` flag to independently gate `execute_query`.
