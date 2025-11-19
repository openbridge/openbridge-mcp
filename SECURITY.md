## Security Posture

This MCP server now ships with a stripped-down Openbridge authentication flow
focused on local deployments. Key behaviours to be aware of:

### Token handling
- `OPENBRIDGE_REFRESH_TOKEN` is exchanged for a JWT on demand and cached in
  memory only; no file persistence remains.
- Failures to convert the refresh token raise an `AuthenticationError`. The
  server never falls back to sending the refresh token downstream.
- Logs redact bearer values. Debug instrumentation reports token length instead
  of full contents.

### Network safeguards
- All HTTP requests use explicit `(connect=10s, read=OPENBRIDGE_API_TIMEOUT)`
  timeouts so an upstream stall cannot hang the MCP indefinitely.
- Pagination helpers enforce host allowlists to reduce SSRF risk when following
  `links.next` responses.

### LLM validation
- SQL text is only sent to an LLM when
  `OPENBRIDGE_ENABLE_LLM_VALIDATION=true`. By default the server evaluates
  queries with heuristics only.

Review the README for configuration details before deploying in your own
environment.
