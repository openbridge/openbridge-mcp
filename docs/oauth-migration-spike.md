# OAuth Migration Spike — Wave D Step 6

> **Status:** Open spike. Decision matrix is filled in based on FastMCP
> source inspection (3.1.0+). Probe outputs against the live Openbridge
> auth API are pending and must be filled in by an engineer with
> production credentials before Step 7 (the migration itself) can start.

## Why this spike exists

`openbridge-mcp` currently authenticates clients via a custom FastMCP
middleware (`src/auth/authentication.py:OpenbridgeAuthMiddleware`). The
middleware extracts an `Authorization: Bearer` token, exchanges Openbridge
refresh tokens for JWTs, and threads the resulting JWT through a
ContextVar to tools.

Wave A made this fail-closed for multi-tenant deployment via
`OPENBRIDGE_REQUIRE_CLIENT_AUTH=true`. That stops cross-tenant leaks
**today**. What it does not do:

- Advertise OAuth 2.0 metadata for clients to discover (RFC 8414 / 9728).
- Verify JWT signatures locally (we currently rely on downstream
  Openbridge APIs to reject bad JWTs — defensible, but it leaves a window
  where a forged JWT consumes server resources before being rejected).
- Surface tenant identity to tools as a `CurrentAccessToken` claim
  object — tools must decode the JWT themselves.
- Support OAuth Dynamic Client Registration (DCR) for MCP clients that
  expect it.

Wave D moves to a FastMCP-native auth provider so all four gaps close.
This document captures the open questions, the decision matrix, and the
exact probes that resolve the unknowns.

## What we need to verify (probes)

Run these against the production Openbridge auth host and paste outputs
into the **Findings** section below.

```bash
# 1. RFC 8414: does the auth server advertise its own metadata?
curl -fsS https://authentication.api.openbridge.io/.well-known/oauth-authorization-server \
  | jq .

# 2. RFC 9728: does it advertise protected resource metadata?
curl -fsS https://authentication.api.openbridge.io/.well-known/oauth-protected-resource \
  | jq .

# 3. JWKS exposure for local JWT verification?
curl -fsS https://authentication.api.openbridge.io/.well-known/jwks.json \
  | jq '.keys | length'

# 4. DCR endpoint? (RFC 7591)
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  -d '{"redirect_uris": ["http://localhost/callback"], "client_name": "spike-probe"}' \
  https://authentication.api.openbridge.io/oauth/register

# 5. Authorization endpoint shape — does the existing /auth/api/ref
#    surface follow OAuth 2.0 token exchange semantics?
#    (Inspect a current refresh-token call:)
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  -d "{\"data\": {\"type\": \"APIAuth\", \"attributes\": {\"refresh_token\": \"$OPENBRIDGE_REFRESH_TOKEN\"}}}" \
  https://authentication.api.openbridge.io/auth/api/ref \
  | jq 'keys, .data.attributes | keys'
```

For each probe, record: HTTP status, presence/absence of expected fields,
and any unexpected shape.

## Decision matrix

The choice between FastMCP's three production auth surfaces depends
entirely on what the probes return.

| Probe outcome | Recommended provider | Why |
|---|---|---|
| RFC 8414 metadata + JWKS + DCR endpoint | **`RemoteAuthProvider`** | Cleanest path. FastMCP composes `JWTVerifier` (for local sig verification against JWKS) with the discovered authorization server URL and emits standard RFC 9728 protected-resource metadata. No custom OAuth surface in our codebase. |
| RFC 8414 metadata + JWKS, no DCR | **`OAuthProxy`** | We can still issue the OAuth flow on the client's behalf via FastMCP's proxy, with explicit `redirect_uri` allowlist + `jwt_signing_key`. JWKS lets us verify locally before forwarding. |
| Refresh-token-only API (current shape, no `/.well-known/...`) | **`MultiAuth(StaticTokenVerifier or JWTVerifier, [our token-exchange shim])`** | Most realistic case given today's `/auth/api/ref` shape. We keep the refresh-token-exchange path (wrapped as a `TokenVerifier` adapter) and layer FastMCP's standard JWT verification for callers who already have a JWT. The custom middleware goes away; the `OpenbridgeAuth` exchange logic moves into a `TokenVerifier` subclass. |

## Class signatures we'll target

From `fastmcp/server/auth/auth.py` (3.1.0+):

```python
class TokenVerifier(AuthProvider):
    async def verify_token(self, token: str) -> AccessToken | None: ...
    @property
    def required_scopes(self) -> list[str] | None: ...
    @property
    def scopes_supported(self) -> list[str] | None: ...

class RemoteAuthProvider(AuthProvider):
    def __init__(
        self,
        token_verifier: TokenVerifier,
        authorization_servers: list[AnyHttpUrl],
        base_url: AnyHttpUrl | str,
        scopes_supported: list[str] | None = None,
        resource_base_url: AnyHttpUrl | str | None = None,
        resource_name: str | None = None,
        resource_documentation: AnyHttpUrl | None = None,
    ): ...

class MultiAuth(AuthProvider):
    """Composes an optional auth server with additional token verifiers."""
```

Wire-up at the FastMCP construction site:

```python
# src/server/mcp_server.py
mcp = FastMCP(
    name="Openbridge MCP",
    instructions="...",
    sampling_handler=sampling_handler,
    auth=<chosen provider>,   # ← this is what changes in Step 7
)
```

The custom `OpenbridgeAuthMiddleware` is removed. Tools read tenant
identity from FastMCP's `CurrentAccessToken` dependency rather than from
our ContextVar:

```python
# src/server/tools/base.py (target shape after migration)
from fastmcp.server.dependencies import get_access_token

def get_auth_headers(ctx=None) -> Dict[str, str]:
    token = get_access_token()    # raises if no auth or token invalid
    return {"Authorization": f"Bearer {token.raw_token}"}
```

## What stays the same

- `src/auth/session_state.py` ContextVar isolation. FastMCP's
  `CurrentAccessToken` is itself a ContextVar; the per-task isolation
  guarantees we lock into Wave A regression tests still apply.
- `OPENBRIDGE_REQUIRE_CLIENT_AUTH` semantics. Even after migration, this
  flag should remain — it controls whether the absence of `auth` on the
  request is a hard failure vs. a server-token fallback.
- `tests/auth/test_multi_tenant.py`. The two-tenant concurrency test
  must still pass against the new auth provider — adapt the test setup
  to use FastMCP's auth dependency surface but keep the assertion shape.

## What we need to throw away

- `src/auth/authentication.py:OpenbridgeAuthMiddleware`. Replaced by
  the chosen FastMCP provider.
- `_resolve_client_token` heuristic (refresh-token vs JWT detection).
  Becomes explicit per-flow in the new provider.
- The "deferred verification" comment at
  `src/auth/authentication.py:177-188`. Local verification is back on.

## Decision gate

Before Step 7 starts, this doc must contain:

1. **Probe outputs** (sections below, currently empty).
2. **Selected provider** based on the matrix.
3. **Migration risk note** — anything in the probe outputs that doesn't
   fit cleanly into FastMCP's expected shapes (custom envelopes,
   non-standard claim names, etc.) and how we'll bridge them.
4. **Backward-compatibility plan** for clients that send refresh tokens
   (`xxx:yyy`) today. Either keep the exchange path as a one-release
   migration, or document a hard cutover with a date.

---

## Findings (TO BE FILLED IN)

### Probe 1 — RFC 8414 metadata
```
<paste output>
```

### Probe 2 — RFC 9728 protected resource metadata
```
<paste output>
```

### Probe 3 — JWKS
```
<paste output>
```

### Probe 4 — DCR endpoint
```
<paste output>
```

### Probe 5 — refresh-token endpoint shape
```
<paste output>
```

### Selected provider
> *Pending probe results.*

### Migration risks
> *Pending probe results.*

### Backward-compatibility plan
> *Pending probe results.*
