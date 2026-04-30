# Cross-Server Envelope Conformance Suite

Validates that envelope JSON emitted by any conforming MCP server matches the v1 contract in `/CONTRACT.md` and the JSON Schema at `/schemas/error-envelope.schema.json`.

## Running

From the `openbridge-mcp` repo root:

```bash
uv run pytest tests/conformance/ -v
```

Requires `jsonschema` as a test dependency. Tests skip gracefully if it is not installed.

## How servers run this suite

Each server repo (`amazon_sp_mcp`, `amazon_ads_mcp`) runs the conformance suite against its tagged release as part of CI. Two integration patterns are supported:

1. **Vendored fixtures + schema** — server repo copies `schemas/error-envelope.schema.json` and the relevant subset of `tests/conformance/fixtures/` into its own test tree (pinned to a specific `openbridge-mcp` commit SHA in CI).
2. **Submodule or git fetch** — server repo CI fetches `openbridge-mcp` at a pinned SHA and runs `pytest tests/conformance/` against fixtures captured from its own tagged build.

For lockstep release validation (Phase 3 and Phase 5), the conformance suite is the merge gate. Both servers must pass against the same suite version on the same calendar day.

## Fixture naming

Fixtures are named `<server>_<scenario>.json`:

- `sp_mcp_input_validation.json` — SP, missing required field
- `sp_api_http_400.json` — SP, upstream HTTP 400
- `sp_api_http_429.json` — SP, upstream HTTP 429 with rate-limit + Retry-After
- `sp_api_client_body_coercion.json` — SP, BodyCoercionError
- `sp_sandbox_runtime.json` — SP, Code Mode sandbox limitation
- `sp_normalized_renamed.json` — SP, success response with `_meta.normalized` rename event
- `sp_normalized_unknown_field.json` — SP, success response with `unknown_field_passed_through` event

Ads fixtures (`ads_*`) added in Phase 2.

## Adding a fixture

1. Capture the actual envelope JSON the server emits for the scenario (do not hand-write — capture from a real test run or a staged sandbox call).
2. Save under `fixtures/<server>_<scenario>.json` with this shape:

   ```json
   {
     "_fixture_meta": {
       "server": "amazon_sp_mcp",
       "scenario": "<short description>",
       "trigger": "<what produces this output>",
       "source_module": "<file path>",
       "source_branch": "<function or branch name>"
     },
     "envelope": { ... } | "response": { ... }
   }
   ```

3. Run `pytest tests/conformance/` — the new fixture will be auto-discovered.
4. If the fixture fails the schema, investigate whether the server output drifted from contract or whether the schema needs updating. **Do not silently fix the fixture** — if the contract changed, that is a versioned event.

## What this suite does NOT do

- It does not stand up live SP or Ads servers. It validates captured fixtures against the schema.
- It does not test cross-server runtime behavior. Live cross-server testing belongs in each server repo's integration tests.
- It does not test hint quality or completeness. Hints are per-server; this suite checks that the `hints` field exists and is an array of strings.
