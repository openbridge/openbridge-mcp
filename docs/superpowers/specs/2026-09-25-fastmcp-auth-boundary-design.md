# FastMCP Authentication Boundary Design

**Date:** 2026-09-25

## Goal

Make FastMCP 4 own request authentication so OAuth clients and clients with
Openbridge API credentials can use the same MCP endpoint without allowing an
invalid credential to reach a tool or become an empty successful result.

## Evidence

The live, credential-safe probes established these constraints:

- `POST /auth/api/ref` accepts an Openbridge API credential and returns `202`
  with an HS256 JWT.
- The API credential itself is not an OAuth access token: introspection returns
  `400` with `active=false`.
- The exchanged JWT is introspectable: introspection returns `200` with
  `active=true`.
- Identity probe, recorded without values: `client_id` present: **no**; `sub`
  present: **no**; `account_id` present: **yes**; `user_id` present: **yes**;
  identity stable across two fresh exchanges: **yes**; uniqueness across two
  accounts: **not tested** because only one development credential was
  available.
- OAuth client probe through the deployed FastMCP endpoint: authorization
  succeeded; FastMCP client token `client_id` present: **yes**; `sub` present:
  **no**; embedded `upstream_claims` present: **no**. The deployed proxy keeps
  the Openbridge access token encrypted server-side, so a client-side probe
  cannot inspect the upstream introspection response.
- The exchanged JWT successfully authorizes a subscriptions API request.
- Openbridge's authentication host does not expose OAuth authorization-server
  metadata, protected-resource metadata, or JWKS at the standard paths.
- The configured Auth0 tenant exposes OIDC discovery and JWKS, but
  `AUTH0_AUDIENCE` is not configured and the application currently integrates
  through Openbridge's OAuth endpoints.

No credential values or Openbridge response data are recorded in this spec.

## Credential Types

The server supports three distinct credentials:

1. **FastMCP OAuth access token.** An MCP client completes the OAuth flow owned
   by `OAuthProxy`. FastMCP issues a reference JWT and swaps it for the upstream
   Openbridge token on each authenticated request.
2. **Openbridge JWT.** A client may already hold a short-lived Openbridge JWT.
   The server validates it through Openbridge's introspection endpoint.
3. **Openbridge API credential.** The long-lived `xxx:yyy` credential is not an
   OAuth access token. The server exchanges it through `/auth/api/ref`, then
   introspects the returned JWT before accepting the request.

All three arrive over HTTP using `Authorization: Bearer ...`; their validation
paths differ.

## Architecture

`OPENBRIDGE_AUTH_MODE=oauth_proxy` remains the primary deployment mode. Build a
FastMCP `MultiAuth` provider with:

- `server=OpenbridgeOAuthProxy(...)`, a narrow `OAuthProxy` subclass that owns
  OAuth routes, discovery metadata,
  authorization-code exchange, FastMCP reference tokens, and upstream token
  refresh;
- `verifiers=[OpenbridgeCredentialVerifier(...)]`, which accepts direct
  Openbridge JWTs and exchanges `xxx:yyy` API credentials.

`OpenbridgeCredentialVerifier` subclasses FastMCP's `TokenVerifier`. For a
colon-form API credential it uses the existing dedicated, bounded,
de-duplicated exchange coordinator to obtain a JWT, then delegates validation
to FastMCP's `IntrospectionTokenVerifier`. For any other token it delegates
directly to that verifier. It then requires the verified, non-empty
`account_id` and `user_id` mapping. It returns `None` for
malformed, inactive, rejected, or unreachable credentials, allowing FastMCP's
bearer boundary to reject the request.

The coordinator implementation remains unchanged. Its dedicated
`ThreadPoolExecutor` prevents slow authentication calls from starving sync MCP
tools in asyncio's default executor; its semaphore and per-key in-flight map
already provide the required bounds and de-duplication.

The shared normalizer writes a stable identity to both `AccessToken.subject`
and `AccessToken.claims["sub"]`, sets a constant Openbridge client identifier,
and preserves FastMCP's existing expiry when `expires_at` is absent. Direct
credentials require the proven non-empty `account_id` plus `user_id` mapping.

OAuthProxy validation remains permissive about claim shape so a valid OAuth
login cannot be rejected merely because Openbridge introspection uses a
different identity schema. `OpenbridgeOAuthProxy.load_access_token()` first
uses the same account/user mapping when available. Otherwise it assigns an
`oauth-session:` subject derived from a SHA-256 digest of the already-verified
FastMCP reference token. The fallback isolates tasks per authenticated OAuth
session without logging or persisting the bearer value. Tasks created under an
OAuth session fallback remain addressable for that FastMCP access token's
lifetime; a newly issued token intentionally receives a new namespace.

This normalization is required because FastMCP Tasks scopes records by
`client_id|sub`; accepting the live API-credential response unchanged would
place every Openbridge caller into the fallback `unknown` namespace. Account
uniqueness was not tested with a second credential, so the implementation also
includes a two-account isolation test using distinct verified claim sets.

The introspection adapter enables a 30-second, 256-entry cache. This bounds the
extra network cost for stateless direct-credential requests while limiting the
revocation window; downstream `401` and `403` handling remains the second
fail-closed check. FastMCP 4.0.10 includes response text in introspection DEBUG
logs, so the application pins that specific library logger to INFO or above.

`OPENBRIDGE_AUTH_MODE=refresh_token` remains as a compatibility mode. When
authentication is enabled, it uses the same `OpenbridgeCredentialVerifier`
directly as `FastMCP(auth=...)`; it no longer uses custom request middleware.
When `AUTH_ENABLED=false`, no FastMCP auth provider is installed and the
existing loopback/insecure-opt-out runtime gate continues to protect local
server-token fallback.

The code-level default for an unset `OPENBRIDGE_AUTH_MODE` remains
`refresh_token` to avoid silently changing existing unconfigured installs.
Production and the supplied environment continue to select `oauth_proxy`
explicitly.

## Native Token Flow

Tools read `fastmcp.server.dependencies.get_access_token()`. The returned
`AccessToken.token` is always the short-lived Openbridge JWT suitable for
downstream API calls:

- OAuthProxy swaps its reference token and returns the verified upstream token.
- Direct JWT introspection returns the submitted Openbridge JWT.
- API-credential exchange introspects and returns the exchanged JWT.

The custom JWT ContextVar, context attributes, and OAuth bridge middleware are
removed from authenticated request paths. FastMCP's access-token context also
provides the normalized identity persisted for background tasks. Code Mode's
nested async tool calls and path-token header injection are covered explicitly
before the legacy propagation layer is deleted.

## Failure Semantics

- Missing bearer token on an auth-enabled MCP endpoint: HTTP `401` or FastMCP's
  equivalent authentication response.
- Malformed, bogus, inactive, or expired bearer token: HTTP `401` or FastMCP's
  equivalent authentication response.
- API credential rejected during exchange: authentication failure at the
  boundary; never server-token fallback.
- Introspection or exchange timeout: fail closed. The response must not contain
  credential material or upstream response bodies.
- Direct-token introspection without stable `account_id` and `user_id`: fail
  closed before FastMCP creates a background-task namespace.
- OAuth introspection without the account/user mapping: retain the valid login
  and isolate its task namespace using the verified FastMCP reference token.
- Downstream `401` or `403` after successful boundary validation: raise the
  Openbridge authentication error envelope rather than returning `[]`.
- Downstream non-auth failures retain each tool's existing contract.

## Compatibility and Security

- OAuth clients continue using browser authorization and FastMCP-issued bearer
  tokens.
- Existing clients with Openbridge JWTs remain supported.
- Existing clients sending `xxx:yyy` API credentials remain supported, but the
  credentials are validated before tool dispatch.
- `OPENBRIDGE_REFRESH_TOKEN` is a server-side API credential. It is not an
  OAuth provider configuration value and is not an automatic identity when
  authentication is enabled.
- `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is deprecated and ignored after this
  migration. Auth-enabled endpoints always require valid client credentials;
  auth-disabled server fallback is governed by `AUTH_ENABLED`, loopback
  binding, and `OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH`.
- The production `.env` selects `oauth_proxy`; `docker-compose.yml` retains its
  `refresh_token` fallback for compatibility with unconfigured installations.
- `AUTH0_*` variables remain unused because the integration continues through
  Openbridge's OAuth proxy endpoints. A future direct `Auth0Provider` migration
  requires a non-empty audience and separate deployment validation.
- Deprecated path-token authentication is valid only in auth-enabled
  `refresh_token` mode, where its injected API credential reaches the native
  verifier. Startup rejects path tokens when authentication is disabled.
- Secrets and tokens must never appear in logs, errors, fixtures, or committed
  probe output.

## Acceptance Criteria

1. OAuthProxy-issued access tokens still authorize MCP requests.
2. A valid direct Openbridge JWT authorizes MCP requests.
3. A valid `xxx:yyy` API credential is exchanged, introspected, and authorizes
   MCP requests.
4. Bogus, malformed, expired, and missing credentials fail before tool code is
   called.
5. A downstream subscriptions `401` or `403` produces an authentication error,
   not `200`, `isError:false`, or `result: []`.
6. Background tasks receive the native FastMCP access-token identity.
7. Two verified direct-credential tenants receive distinct FastMCP task scopes;
   OAuth sessions without account/user claims receive distinct hashed session
   scopes and remain authorized.
8. OAuth discovery routes continue to be supplied by OAuthProxy when wrapped
   in `MultiAuth`.
9. Code Mode nested calls and enabled path tokens preserve the authenticated
   caller through the native verifier.
10. `make lint`, `make check`, and `make test` pass in that order.
