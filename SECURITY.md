## Security Posture

This MCP server supports local and remotely hosted deployments with fail-closed
runtime validation. Key security behaviors:

### Token handling (Dual-Mode)
The server supports two authentication modes:

**Server-side authentication:**
- `OPENBRIDGE_REFRESH_TOKEN` (optional) is exchanged for a JWT on demand and cached in
  memory only; no file persistence.
- **CRITICAL**: Never commit `OPENBRIDGE_REFRESH_TOKEN` to version control.
  Store it only in your local `.env` file (gitignored).
- Failures to convert the refresh token raise an `AuthenticationError`. The
  server never falls back to sending the refresh token downstream.

**Client-side authentication:**
- Clients can provide `Authorization: Bearer <token>` headers directly.
- Client-provided tokens take precedence over server-side tokens.
- The server extracts client tokens from HTTP headers and uses them for API calls.
- **IMPORTANT**: Client tokens should also never be committed to version control.
  Pass them via environment variables in client configurations.

**General:**
- Logs redact bearer values. Debug instrumentation reports token length instead
  of full contents.
- Server starts successfully without `OPENBRIDGE_REFRESH_TOKEN`, enabling pure
  client-side authentication deployments.
- Remote listeners require client authentication by default. Server-principal
  fallback requires an explicitly dangerous override and should remain private
  to a deliberately isolated single-tenant deployment.
- OAuth proxy mode on a remote listener requires a stable non-placeholder
  signing key containing at least 32 bytes.

### Network safeguards
- All HTTP requests use explicit `(connect=10s, read=OPENBRIDGE_API_TIMEOUT)`
  timeouts so an upstream stall cannot hang the MCP indefinitely.
- Pagination helpers enforce host allowlists to reduce SSRF risk when following
  `links.next` responses.
- Uvicorn caps open connections with `MCP_LIMIT_CONCURRENCY` (default 100),
  while the bundled Caddy ingress rejects request bodies over 1 MB and omits
  request URIs from access logs. Add principal/IP rate limiting at the
  production ingress or WAF.

### LLM validation
- SQL text is only sent to an LLM when
  `OPENBRIDGE_ENABLE_LLM_VALIDATION=true`. By default the server evaluates
  queries with heuristics only.
- SQL execution is absent unless `OPENBRIDGE_ENABLE_QUERY_EXECUTION=true` and
  an API key is configured.

### Privileged catalog

Credential-returning and mutation tools are absent by default. Enable them only
with `OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS=true` for trusted clients with upstream
authorization. Code Mode can reach enabled privileged operations through its
single `execute` bridge, so the flag does not replace user confirmation or
fine-grained authorization.

Legacy credential-bearing path tokens are disabled by default. Their age and
logging mitigations do not eliminate URL exposure; use normal OAuth or bearer
headers for new deployments.

## Security Best Practices

When deploying this MCP server:

1. **Never commit secrets**: Ensure `.env` is in `.gitignore` and never committed
2. **Rotate tokens regularly**: Refresh your `OPENBRIDGE_REFRESH_TOKEN` periodically
3. **Use environment variables**: Store all sensitive configuration in environment
   variables, never hardcode in source files
4. **Review logs carefully**: Verify logs don't expose sensitive data before
   sharing or storing long-term
5. **Keep dependencies updated**: Regularly update Python packages to address
   known vulnerabilities

## Responsible Disclosure

We take security vulnerabilities seriously. If you discover a security issue:

1. **Do NOT** open a public GitHub issue
2. Email your findings to: **support@openbridge.com**
3. Include:
   - Detailed description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Suggested remediation (if available)

We will acknowledge receipt within 48 hours and provide a timeline for
remediation. We appreciate responsible disclosure and will credit researchers
(with permission) in security advisories.

## Additional Resources

Review the README for configuration details before deploying in your own
environment.
