## Security Posture

This MCP server ships with a streamlined Openbridge authentication flow
focused on local deployments. Key security behaviors:

### Token handling
- `OPENBRIDGE_REFRESH_TOKEN` is exchanged for a JWT on demand and cached in
  memory only; no file persistence.
- **CRITICAL**: Never commit `OPENBRIDGE_REFRESH_TOKEN` to version control.
  Store it only in your local `.env` file (gitignored).
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
