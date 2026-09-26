# FastMCP OAuth Migration Decision

> **Status:** Closed. FastMCP 4 owns the authentication boundary through
> `MultiAuth(OpenbridgeOAuthProxy, OpenbridgeCredentialVerifier)`.

## Decision

`OPENBRIDGE_AUTH_MODE=oauth_proxy` is the primary production/client mode. It
combines two FastMCP-native paths:

- `OpenbridgeOAuthProxy` owns OAuth discovery, authorization, callbacks, and
  reference-token validation.
- `OpenbridgeCredentialVerifier` accepts a direct Openbridge JWT or exchanges
  an `xxx:yyy` Openbridge API credential, then introspects the resulting JWT.

Direct credentials require non-empty `account_id` and `user_id` claims. OAuth
tokens use that mapping when available. A valid OAuth token without those
claims receives an isolated task subject derived from the verified FastMCP
reference token, preventing unrelated OAuth sessions from sharing a task
namespace.

The code and Compose unset-variable default remains `refresh_token` for
compatibility. In that mode FastMCP installs only the direct-credential
verifier. Auth-enabled requests always require a valid client Bearer
credential; `OPENBRIDGE_REFRESH_TOKEN` is limited to auth-disabled local or
isolated single-tenant fallback.

The complete architecture and failure semantics are in the
[FastMCP authentication boundary design](superpowers/specs/2026-09-25-fastmcp-auth-boundary-design.md).

## Sanitized evidence

Live probes recorded only status and claim presence:

| Probe | Result |
|---|---|
| Standard authorization-server metadata | Absent |
| Standard protected-resource metadata | Absent |
| Standard JWKS | Absent |
| Configured Auth0 discovery and JWKS | Present |
| API credential introspection | Inactive / not introspectable |
| JWT returned by API-credential exchange | Active / introspectable |
| Exchanged JWT `client_id` | Absent |
| Exchanged JWT `sub` | Absent |
| Exchanged JWT `account_id` | Present |
| Exchanged JWT `user_id` | Present |
| Account/user identity across two fresh exchanges | Stable |
| FastMCP OAuth token `client_id` | Present |
| FastMCP OAuth token `sub` | Absent |
| FastMCP OAuth token `upstream_claims` | Absent |

Only one development API credential was available, so live uniqueness across
two accounts was not probed. Automated tests cover distinct verified account
and user identities and the OAuth session fallback.

No tokens, account identifiers, credential-bearing URLs, or API response
bodies are recorded here.

## Operational contract

- OAuth is the preferred client flow. Direct JWT and API-credential Bearer
  tokens remain supported on the same MCP endpoint.
- Invalid, missing, inactive, or unreachable credentials fail before tool
  dispatch.
- Successful introspection is cached for 30 seconds with a 256-entry cap.
- Downstream Openbridge `401` and `403` responses remain authentication
  failures and never become empty successful tool results.
- `AUTH0_*` settings do not configure this integration. OAuthProxy uses the
  Openbridge endpoints and `OPENBRIDGE_OAUTH_*` credentials.
- `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is deprecated and ignored. Remove it from
  deployment configuration.
