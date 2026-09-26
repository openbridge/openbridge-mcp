# FastMCP Authentication Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authenticate OAuth, direct Openbridge JWT, and Openbridge API-credential clients through FastMCP 4 before any MCP tool executes.

**Architecture:** Keep a narrow `OpenbridgeOAuthProxy(OAuthProxy)` as the OAuth server and compose it with an `OpenbridgeCredentialVerifier` using FastMCP `MultiAuth`. Direct credentials require verified account/user identity. OAuth uses that identity when present and otherwise derives an isolated task subject from the already-verified FastMCP reference token, so claim-shape differences cannot reject valid logins or merge tenant task namespaces.

**Tech Stack:** Python 3.13+, FastMCP 4.0.5, MCP 2.2, httpx, requests, pytest, pytest-asyncio, Ruff

**Spec:** `docs/superpowers/specs/2026-09-25-fastmcp-auth-boundary-design.md`

## Global Constraints

- Keep `OPENBRIDGE_AUTH_MODE=oauth_proxy` as the explicitly configured
  production `.env` profile; do not replace it with direct Auth0 integration.
- Keep Docker Compose's unset-variable fallback at `refresh_token` for
  compatibility and document that it differs from the production `.env`.
- Keep the unset-variable compatibility default of `refresh_token` for existing deployments.
- Use FastMCP `AuthProvider`, `MultiAuth`, `TokenVerifier`, `IntrospectionTokenVerifier`, `AccessToken`, and `get_access_token()` primitives.
- Never log or persist raw API credentials, JWTs, OAuth client secrets, or upstream response bodies.
- Treat `xxx:yyy` as an Openbridge API credential that must be exchanged; do not describe it as an OAuth refresh token.
- An auth-enabled deployment must fail closed before tool dispatch for missing or invalid credentials.
- Normalize verified direct-token `account_id` and `user_id` into FastMCP's
  `sub`; reject direct tokens without both non-empty values.
- Never reject a valid OAuthProxy login solely because upstream introspection
  omits those fields. Fall back to a SHA-256 subject derived from the verified
  FastMCP reference token.
- Cache successful introspection for 30 seconds with at most 256 entries, and
  keep FastMCP's introspection logger above DEBUG to prevent response-body logs.
- Preserve `AUTH_ENABLED=false` only for guarded local/single-tenant operation.
- Deprecate `OPENBRIDGE_REQUIRE_CLIENT_AUTH`; native auth always requires a
  client credential when `AUTH_ENABLED=true`.
- Run `make lint`, `make check`, and `make test` in that exact order before committing.
- Use the repository-required Claude attribution trailer on every commit.

## Review Focus

- Direct tokens must produce a stable, non-empty account/user subject; OAuth
  tokens must produce either that subject or an isolated session subject.
- A non-JWT opaque bearer without a colon must be introspected and rejected
  rather than exchanged or passed through.
- Any exception during API-credential exchange, including JWT decoding errors,
  must become an authentication failure rather than HTTP 500.
- Code Mode nested tool calls and path-token requests must retain the native
  FastMCP access-token context after the custom ContextVar is deleted.
- A token revoked after cached boundary validation must surface a downstream
  `401/403` authentication error rather than an empty inventory.

---

### Task 0: Verify the deployed OAuth client boundary

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-fastmcp-auth-boundary-design.md`

**Interfaces:**
- Consumes: a normal FastMCP OAuth client authorization against the deployed
  MCP endpoint.
- Produces: sanitized evidence about the client-visible reference token and the
  safe OAuth fallback requirement implemented in Task 2.

- [x] **Step 1: Complete the production-faithful OAuth flow**

Use FastMCP's OAuth client against the deployed `/mcp` endpoint. The flow must
use the deployed allowlisted Openbridge callback and return to the client's
localhost callback. Keep the resulting bearer token in memory only.

- [x] **Step 2: Record client-visible claim presence only**

Observed: authorization succeeded; `client_id` present: yes; `sub` present: no;
embedded `upstream_claims` present: no. No token, authorization code, identity
value, or response body was printed or persisted.

- [x] **Step 3: Select the non-rejecting OAuth fallback**

The client-visible token is a FastMCP reference token; the upstream Openbridge
token remains encrypted inside the deployed proxy. Task 2 therefore accepts a
successfully validated OAuth token regardless of upstream identity claim shape,
using verified account/user identity when available and otherwise hashing the
FastMCP reference token into an `oauth-session:` subject. This task has no code
commit.

---

### Task 1: Implement the FastMCP-native Openbridge credential verifier

**Files:**
- Create: `src/auth/openbridge_verifier.py`
- Create: `tests/auth/test_openbridge_verifier.py`
- Modify: `tests/auth/test_exchange_coordinator.py`

**Interfaces:**
- Consumes: the existing `OpenbridgeAuth.exchange_token(refresh_token: str) -> str`, `is_refresh_token(token: str) -> bool`, and dedicated-pool `TokenExchangeCoordinator.resolve(key: str, exchange: Callable[[], str]) -> Awaitable[str]`.
- Produces: `normalize_verified_identity(access_token: AccessToken) -> AccessToken | None`, `OpenbridgeCredentialVerifier`, `create_openbridge_introspection_verifier(base_url: str | None = None) -> IntrospectionTokenVerifier`, and `create_openbridge_credential_verifier(base_url: str | None = None, introspection: IntrospectionTokenVerifier | None = None) -> OpenbridgeCredentialVerifier`.

- [ ] **Step 1: Write verifier classification and rejection tests**

Add async tests that inject fake exchange and FastMCP introspection
collaborators. Every successful fake introspection response must carry the same
shape observed live:

```python
@pytest.mark.asyncio
async def test_api_credential_is_exchanged_then_introspected():
    introspection = AsyncMock()
    introspection.verify_token.return_value = AccessToken(
        token="verified.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"active": True, "account_id": 101, "user_id": 202},
    )
    auth = MagicMock()
    auth.exchange_token.return_value = "verified.jwt.token"
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    result = await verifier.verify_token("account123:api-secret")

    assert result is not None
    assert result.token == "verified.jwt.token"
    assert result.client_id == "openbridge"
    assert result.subject == "account:101|user:202"
    assert result.claims["sub"] == "account:101|user:202"
    auth.exchange_token.assert_called_once_with("account123:api-secret")
    introspection.verify_token.assert_awaited_once_with("verified.jwt.token")


@pytest.mark.asyncio
async def test_direct_jwt_is_introspected_without_exchange():
    introspection = AsyncMock()
    introspection.verify_token.return_value = AccessToken(
        token="header.payload.signature",
        client_id="unknown",
        scopes=[],
        claims={"active": True, "account_id": 101, "user_id": 202},
    )
    auth = MagicMock()
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    result = await verifier.verify_token("header.payload.signature")

    assert result is not None
    auth.exchange_token.assert_not_called()
    introspection.verify_token.assert_awaited_once_with("header.payload.signature")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ["bogus-token", "account123:bad-secret", "header.payload.bad"],
)
async def test_invalid_credentials_return_none(token):
    introspection = AsyncMock()
    introspection.verify_token.return_value = None
    auth = MagicMock()
    if ":" in token:
        auth.exchange_token.side_effect = AuthenticationError("rejected")
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    assert await verifier.verify_token(token) is None
```

Also test that an exchanged-but-inactive JWT returns `None`; missing, `None`,
empty, and whitespace-only identity components return `None`; two claim sets
for different accounts produce distinct subjects/task scopes; `expires_at` is
copied into `AccessToken.expires_at`; an existing `verified.expires_at` is
preserved when the Openbridge-specific claim is absent; every exchange or
introspection exception returns `None`; and log records contain neither the
submitted credential nor returned JWT. The exchange-exception test must use
`jwt.DecodeError` because `OpenbridgeAuth.exchange_token()` can raise it after
the HTTP exchange succeeds. Assert the warning contains only the exception type
and fixed text.
Test the factory separately: its returned FastMCP verifier must have a 30-second TTL, a
256-entry cap, and the FastMCP introspection logger must have an effective level
of INFO or higher even when application logging is DEBUG.

- [ ] **Step 2: Run the verifier tests and confirm RED**

Run:

```bash
.venv/bin/pytest tests/auth/test_openbridge_verifier.py -v
```

Expected: collection fails because `src.auth.openbridge_verifier` does not exist.

- [ ] **Step 3: Write the verifier using FastMCP primitives**

Create a focused module with this public shape. FastMCP performs introspection;
the direct-credential verifier then normalizes only verified claims:

```python
def normalize_verified_identity(access_token: AccessToken) -> AccessToken | None:
    account_id = str(access_token.claims.get("account_id") or "").strip()
    user_id = str(access_token.claims.get("user_id") or "").strip()
    if not account_id or not user_id:
        return None
    subject = f"account:{account_id}|user:{user_id}"
    expires_at = (
        access_token.claims.get("expires_at") or access_token.expires_at
    )
    return access_token.model_copy(
        update={
            "client_id": "openbridge",
            "subject": subject,
            "expires_at": int(expires_at) if expires_at else None,
            "claims": {**access_token.claims, "sub": subject},
        }
    )


class OpenbridgeCredentialVerifier(TokenVerifier):
    def __init__(
        self,
        *,
        auth: OpenbridgeAuth,
        introspection: IntrospectionTokenVerifier,
        exchange_coordinator: TokenExchangeCoordinator | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url)
        self._auth = auth
        self._introspection = introspection
        self._exchange_coordinator = (
            exchange_coordinator or TokenExchangeCoordinator()
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        candidate = token
        if is_refresh_token(token):
            try:
                candidate = await self._exchange_coordinator.resolve(
                    credential_cache_key(token),
                    lambda: self._auth.exchange_token(token),
                )
            except Exception as exc:
                logger.warning(
                    "Openbridge API credential exchange failed (%s)",
                    type(exc).__name__,
                )
                return None
        try:
            verified = await self._introspection.verify_token(candidate)
        except Exception:
            logger.warning("Openbridge credential validation failed")
            return None
        if verified is None:
            return None
        return normalize_verified_identity(verified)


def credential_cache_key(token: str) -> str:
    return "credential:" + hashlib.sha256(token.encode()).hexdigest()


def create_openbridge_introspection_verifier(
    *, base_url: str | None = None
) -> IntrospectionTokenVerifier:
    library_logger = logging.getLogger(
        "fastmcp.server.auth.providers.introspection"
    )
    if library_logger.getEffectiveLevel() < logging.INFO:
        library_logger.setLevel(logging.INFO)
    auth_base_url = os.getenv(
        "OPENBRIDGE_AUTH_BASE_URL",
        "https://authentication.api.openbridge.io",
    ).rstrip("/")
    return IntrospectionTokenVerifier(
        introspection_url=f"{auth_base_url}/auth/oauth/introspect",
        client_id=os.getenv("OPENBRIDGE_OAUTH_CLIENT_ID", "openbridge-mcp"),
        client_secret=os.getenv("OPENBRIDGE_OAUTH_CLIENT_SECRET", "not-used"),
        client_auth_method="client_secret_post",
        cache_ttl_seconds=30,
        max_cache_size=256,
        base_url=base_url,
    )


def create_openbridge_credential_verifier(
    *,
    base_url: str | None = None,
    introspection: IntrospectionTokenVerifier | None = None,
) -> OpenbridgeCredentialVerifier:
    verifier = introspection or create_openbridge_introspection_verifier(
        base_url=base_url
    )
    return OpenbridgeCredentialVerifier(
        auth=get_auth(),
        introspection=verifier,
        base_url=base_url,
    )
```

Use a SHA-256 digest for `credential_cache_key`; never put credential text in
the coordinator key, logs, or exception messages. Construct FastMCP's delegate
from `OPENBRIDGE_AUTH_BASE_URL`, `OPENBRIDGE_OAUTH_CLIENT_ID`, and
`OPENBRIDGE_OAUTH_CLIENT_SECRET`, using `client_secret_post`,
`cache_ttl_seconds=30`, and `max_cache_size=256`. Set only the
`fastmcp.server.auth.providers.introspection` logger to INFO when its effective
level would otherwise allow DEBUG, because FastMCP 4.0.5 logs the first 200
characters of non-200 response bodies at DEBUG.

- [ ] **Step 4: Retarget the existing coordinator integration test**

Keep the existing coordinator bound, dedicated executor, timeout, and
de-duplication tests unchanged. Rewrite only the test that imports
`OpenbridgeAuthMiddleware` so it drives `OpenbridgeCredentialVerifier` with a
blocking fake exchange. Use `release.wait(timeout=1)` and assert the event loop
remains responsive. Add one assertion that simultaneous verifier calls for the
same API credential submit one digest-keyed exchange.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest tests/auth/test_openbridge_verifier.py tests/auth/test_exchange_coordinator.py -v
```

Expected: all tests pass with no credential text in captured logs.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/auth/openbridge_verifier.py tests/auth/test_openbridge_verifier.py tests/auth/test_exchange_coordinator.py
git commit -m "feat: add native Openbridge token verifier" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 2: Compose OAuth and direct credentials with MultiAuth

**Files:**
- Modify: `src/auth/oauth_proxy.py`
- Modify: `src/server/mcp_server.py`
- Modify: `tests/auth/test_oauth_proxy.py`
- Modify: `tests/server/test_mcp_server.py`

**Interfaces:**
- Consumes: both factories and `normalize_verified_identity()` from Task 1.
- Produces: `OpenbridgeOAuthProxy(OAuthProxy)`, `create_oauth_proxy(base_url: str, token_verifier: TokenVerifier | None = None) -> OpenbridgeOAuthProxy`, `create_oauth_auth(base_url: str) -> MultiAuth`, and FastMCP servers whose `auth` provider is native in both enabled modes.

- [ ] **Step 1: Write failing provider-composition tests**

Add tests that assert:

```python
def test_create_oauth_auth_composes_proxy_and_openbridge_verifier(monkeypatch):
    auth = create_oauth_auth(base_url="https://mcp.example.test")

    assert isinstance(auth, MultiAuth)
    assert isinstance(auth.server, OpenbridgeOAuthProxy)
    assert len(auth.verifiers) == 1
    assert isinstance(auth.verifiers[0], OpenbridgeCredentialVerifier)
```

Add integration assertions that `create_mcp_server()` passes `MultiAuth` to
FastMCP in `oauth_proxy` mode, passes `OpenbridgeCredentialVerifier` directly
in enabled `refresh_token` mode, and passes no auth provider when
`AUTH_ENABLED=false`. Assert OAuth well-known and callback routes remain present
when `MultiAuth` wraps OAuthProxy.

Add async OAuth identity tests for both branches:

```python
@pytest.mark.asyncio
async def test_oauth_proxy_uses_verified_account_identity(proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"account_id": 101, "user_id": 202},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    result = await proxy.load_access_token("fastmcp.reference.token")

    assert result.subject == "account:101|user:202"


@pytest.mark.asyncio
async def test_oauth_proxy_falls_back_to_isolated_session(proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"active": True},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    first = await proxy.load_access_token("fastmcp.reference.one")
    repeated = await proxy.load_access_token("fastmcp.reference.one")
    other = await proxy.load_access_token("fastmcp.reference.two")

    assert first.subject == repeated.subject
    assert first.subject.startswith("oauth-session:")
    assert first.subject != other.subject
    assert first.claims["sub"] == first.subject
```

Also assert `None` from `OAuthProxy.load_access_token()` remains `None`, the
reference token never appears in logs, and the fallback preserves the verified
upstream token and expiry.

- [ ] **Step 2: Run the composition tests and confirm RED**

Run:

```bash
.venv/bin/pytest tests/auth/test_oauth_proxy.py tests/server/test_mcp_server.py -v
```

Expected: failures because `create_oauth_auth` is absent and refresh-token mode
still installs `OpenbridgeAuthMiddleware`.

- [ ] **Step 3: Implement native provider composition**

In `src/auth/oauth_proxy.py`, add the non-rejecting OAuth identity wrapper:

```python
class OpenbridgeOAuthProxy(OAuthProxy):
    async def load_access_token(self, token: str) -> AccessToken | None:
        verified = await super().load_access_token(token)
        if verified is None:
            return None
        normalized = normalize_verified_identity(verified)
        if normalized is not None:
            return normalized
        digest = hashlib.sha256(token.encode()).hexdigest()
        subject = f"oauth-session:{digest}"
        return verified.model_copy(
            update={
                "client_id": "openbridge-oauth",
                "subject": subject,
                "claims": {**verified.claims, "sub": subject},
            }
        )
```

Construct `OpenbridgeOAuthProxy` in `create_oauth_proxy`. Its
`token_verifier` is the plain FastMCP introspection verifier from Task 1, so
active OAuth tokens are accepted before identity normalization.

Then add the composition factory:

```python
def create_oauth_auth(*, base_url: str) -> MultiAuth:
    introspection = create_openbridge_introspection_verifier(base_url=base_url)
    return MultiAuth(
        server=create_oauth_proxy(
            base_url=base_url,
            token_verifier=introspection,
        ),
        verifiers=[
            create_openbridge_credential_verifier(
                base_url=base_url,
                introspection=introspection,
            )
        ],
        base_url=base_url,
    )
```

Update `create_oauth_proxy` to accept the optional verifier and use it as the
OAuthProxy upstream validator. When omitted, construct the same FastMCP
introspection verifier. OAuth and direct credentials share its 30-second cache,
while each caller receives a copied, route-appropriate identity model.

In `create_mcp_server()`:

- construct FastMCP with the existing `_MCP_KWARGS` plus
  `auth=create_oauth_auth(base_url=base_url)` for enabled `oauth_proxy` mode;
- construct FastMCP with `_MCP_KWARGS` plus
  `auth=create_openbridge_credential_verifier()` for enabled
  `refresh_token` mode;
- construct FastMCP with only `_MCP_KWARGS` when `AUTH_ENABLED=false`;
- keep `ErrorEnvelopeMiddleware` in all modes;
- stop registering `OpenbridgeAuthMiddleware` and `OAuthBridgeMiddleware`.

- [ ] **Step 4: Add boundary-level invalid credential tests**

Use a real FastMCP HTTP application with a spy tool and assert that missing,
bogus, malformed, inactive, and failed-exchange bearer credentials receive
`401` or FastMCP's authentication JSON-RPC failure and the spy tool call count
remains zero. Add positive cases for a mocked OAuthProxy reference token,
direct JWT, and API credential.

- [ ] **Step 5: Run focused integration tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest tests/auth/test_oauth_proxy.py tests/server/test_mcp_server.py tests/test_security_fixes.py -v
```

Expected: all authentication boundary and server construction tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/auth/oauth_proxy.py src/server/mcp_server.py tests/auth/test_oauth_proxy.py tests/server/test_mcp_server.py tests/test_security_fixes.py
git commit -m "feat: enforce FastMCP authentication boundary" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 3: Remove custom token propagation and use native access-token context

**Files:**
- Modify: `src/server/tools/base.py`
- Modify: `src/server/mcp_server.py`
- Modify: `src/auth/authentication.py`
- Modify: `src/auth/oauth_proxy.py`
- Modify: `src/utils/runtime_security.py`
- Delete: `src/auth/session_state.py`
- Delete: `tests/auth/test_session_state.py`
- Modify: `tests/auth/test_exchange_coordinator.py`
- Modify: `tests/server/tools/test_base.py`
- Modify: `tests/auth/test_authentication.py`
- Modify: `tests/auth/test_oauth_proxy.py`
- Modify: `tests/auth/test_multi_tenant.py`
- Modify: `tests/auth/test_path_token_middleware.py`
- Modify: `tests/server/test_code_mode_integration.py`
- Modify: `tests/server/test_tasks.py`
- Modify: `tests/utils/test_runtime_security.py`

**Interfaces:**
- Consumes: FastMCP `get_access_token() -> AccessToken | None` populated by the providers in Task 2.
- Produces: `get_auth_headers(ctx: Context | None = None) -> dict[str, str]` backed by native FastMCP auth, with server fallback only when authentication is disabled and runtime security permits it.

- [ ] **Step 1: Write failing native-context tests**

Replace middleware-state assertions with tests that set FastMCP's authenticated
user context and assert:

```python
def test_get_auth_headers_uses_native_access_token(monkeypatch):
    monkeypatch.setattr(
        "src.server.tools.base.get_access_token",
        lambda: AccessToken(token="verified.jwt.token", client_id="ob", scopes=[]),
    )

    assert get_auth_headers() == {"Authorization": "Bearer verified.jwt.token"}
```

Add tests proving context attributes and `session_state` are no longer read,
auth-enabled missing native context raises `AuthenticationError`, and
auth-disabled guarded local mode may still call `get_auth().get_headers()`.
Add a configuration test proving `OPENBRIDGE_REQUIRE_CLIENT_AUTH=false` does not
permit missing credentials when authentication is enabled.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
.venv/bin/pytest tests/server/tools/test_base.py tests/auth/test_authentication.py tests/auth/test_oauth_proxy.py tests/auth/test_multi_tenant.py tests/auth/test_exchange_coordinator.py tests/auth/test_session_state.py -v
```

Expected: failures because `_get_context_jwt`, custom middleware, and bridge
state are still present.

- [ ] **Step 3: Simplify tool authentication**

Make `get_auth_headers()` first read native `get_access_token()`. If present,
return its `token`. If absent while authentication is enabled, raise
`AuthenticationError`. Permit `OPENBRIDGE_REFRESH_TOKEN` server fallback only
when `AUTH_ENABLED=false`; rely on `validate_runtime_security()` for the
loopback or explicit-dangerous-opt-out requirement.

- [ ] **Step 4: Remove obsolete middleware and ContextVar code**

Delete `OpenbridgeAuthMiddleware`, `_resolve_client_token`,
`_set_fastmcp_task_identity`, `_set_context_state`, `OAuthBridgeMiddleware`, and
the request-JWT session-state module and its dedicated test file. Rewrite the
remaining middleware-based test in `tests/auth/test_exchange_coordinator.py` to
exercise `OpenbridgeCredentialVerifier`, as specified in Task 1. Retain
`AuthConfig`, auth-mode parsing,
`AUTH_ERROR_CODE` if still used by tool error translation, and the
`OpenbridgeAuth` exchange/cache implementation used by the native verifier.
Update imports and `__all__` accordingly.

- [ ] **Step 5: Deprecate the superseded client-auth flag**

Remove `require_client_auth` from `AuthConfig`, tool fallback decisions,
`_warn_if_server_token_fallback_open()`, and `validate_runtime_security()`. The remote-risk expression becomes solely
`not auth_enabled` because an auth-enabled FastMCP endpoint cannot perform
server-principal fallback. If `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is present,
emit one startup deprecation warning explaining that the value is ignored and
that `AUTH_ENABLED=true` always requires a client credential. Update
`tests/utils/test_runtime_security.py` to cover the simplified gate and warning.
Also reject `MCP_PATH_TOKEN_ENABLED=true` when `AUTH_ENABLED=false`; otherwise
the injected API credential would bypass the absent verifier and the tool layer
could use the server credential instead.

- [ ] **Step 6: Preserve task, Code Mode, and path-token identity**

Adapt task tests to assert FastMCP snapshots the verifier-produced
`AccessToken.subject` and claims. Cover two credentials concurrently and assert
their `client_id|sub` task scopes do not cross. Add a Code Mode integration test
where `execute` invokes a nested spy tool and that tool reads the same JWT from
`get_access_token()`. Add a path-token integration test proving the middleware's
injected `Bearer xxx:yyy` header reaches `OpenbridgeCredentialVerifier`, is
exchanged, and becomes the native JWT context.

- [ ] **Step 7: Run focused tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest tests/server/tools/test_base.py tests/auth/test_authentication.py tests/auth/test_oauth_proxy.py tests/auth/test_multi_tenant.py tests/auth/test_exchange_coordinator.py tests/auth/test_path_token_middleware.py tests/server/test_code_mode_integration.py tests/server/test_tasks.py tests/utils/test_runtime_security.py -v
```

Expected: all tests pass using only native FastMCP request identity.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/server/tools/base.py src/server/mcp_server.py src/auth/authentication.py src/auth/oauth_proxy.py src/utils/runtime_security.py tests/server/tools/test_base.py tests/auth/test_authentication.py tests/auth/test_oauth_proxy.py tests/auth/test_multi_tenant.py tests/auth/test_exchange_coordinator.py tests/auth/test_path_token_middleware.py tests/server/test_code_mode_integration.py tests/server/test_tasks.py tests/utils/test_runtime_security.py
git rm src/auth/session_state.py tests/auth/test_session_state.py
git commit -m "refactor: use FastMCP access token context" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 4: Stop subscriptions from swallowing downstream authorization failures

**Files:**
- Modify: `src/server/tools/base.py`
- Modify: `src/server/tools/subscriptions.py`
- Modify: `tests/server/tools/test_base.py`
- Modify: `tests/server/tools/test_subscriptions.py`

**Interfaces:**
- Produces: `raise_for_auth_status(response, *, tool: str, operation: str) -> None`, raising a FastMCP `ToolError` carrying the v1 `auth_error` envelope for `401` and `403` without including response bodies.
- Consumes: the helper from every subscriptions response branch that currently converts a non-200 response into an empty or partial success.

- [ ] **Step 1: Write failing downstream auth tests**

Add parameterized tests:

```python
@pytest.mark.parametrize("status_code", [401, 403])
def test_get_subscriptions_raises_on_auth_failure(
    status_code, monkeypatch, mock_auth_headers, mock_subscriptions_api
):
    monkeypatch.setattr(
        subscriptions.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=status_code,
            text="sensitive upstream body",
        ),
    )

    with pytest.raises(ToolError) as exc_info:
        subscriptions.get_subscriptions()

    assert json.loads(str(exc_info.value))["error_kind"] == "auth_error"
```

Assert the exception and captured logs exclude `sensitive upstream body`.
Cover the primary list, item lookup, storage list, and SPM lookup paths.

- [ ] **Step 2: Run subscription tests and confirm RED**

Run:

```bash
.venv/bin/pytest tests/server/tools/test_subscriptions.py -v
```

Expected: the list case returns `[]` instead of raising.

- [ ] **Step 3: Implement the auth-status helper and apply it**

Add this behavior before existing status handling:

```python
def raise_for_auth_status(response, *, tool: str, operation: str) -> None:
    if response.status_code in {401, 403}:
        envelope = auth_error(
            tool=tool,
            summary=f"Openbridge authorization failed during {operation}",
            hints=["Reconnect with a valid credential, then retry."],
        )
        raise ToolError(json.dumps(envelope))
```

Call it for each subscriptions HTTP response before a branch that returns an
empty value or generic error. Remove logging of `response.text` from those auth
branches. Preserve existing behavior for `404`, `429`, and `5xx` responses.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest tests/server/tools/test_base.py tests/server/tools/test_subscriptions.py -v
```

Expected: all tests pass; `401/403` cannot become an empty success.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/server/tools/base.py src/server/tools/subscriptions.py tests/server/tools/test_base.py tests/server/tools/test_subscriptions.py
git commit -m "fix: surface downstream authorization failures" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 5: Document OAuth-primary dual credential authentication

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docker-compose.yml`
- Modify: `docs/oauth-migration-spike.md`
- Test: `tests/auth/test_authentication.py`

**Interfaces:**
- Documents: `OPENBRIDGE_AUTH_MODE`, `OPENBRIDGE_REFRESH_TOKEN`, `OPENBRIDGE_OAUTH_CLIENT_ID`, `OPENBRIDGE_OAUTH_CLIENT_SECRET`, and the three credential flows from the design spec.

- [ ] **Step 1: Add configuration-contract tests**

Assert the unset auth-mode compatibility default remains `refresh_token`, an
explicit `oauth_proxy` value selects OAuth, and `AUTH_ENABLED=false` suppresses
provider construction. Add a focused config assertion that the compose
deployment passes `OPENBRIDGE_AUTH_MODE` without embedding a credential value.

- [ ] **Step 2: Update operator documentation**

Document these exact distinctions:

- OAuth is the primary production/client flow and yields a bearer token.
- Direct Openbridge JWT bearer tokens are accepted after introspection.
- `xxx:yyy` is an Openbridge API credential; it is exchanged and then
  introspected before request dispatch.
- `OPENBRIDGE_REFRESH_TOKEN` is only an auth-disabled local/single-tenant server
  fallback; auth-enabled requests never silently assume it.
- `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is deprecated and ignored. Remove it from
  `.env.example` and Compose configuration examples; retain a migration note
  that `AUTH_ENABLED=true` now always requires a valid client credential.
- `AUTH0_*` variables do not configure the current integration; OAuthProxy uses
  the Openbridge endpoints and `OPENBRIDGE_OAUTH_*` client credentials.
- Invalid or missing bearer credentials fail at the FastMCP boundary.
- Successful introspection is cached for 30 seconds with a 256-entry cap; a
  downstream `401/403` still forces an authentication failure.
- The production `.env` selects `oauth_proxy`, while Compose intentionally
  retains `refresh_token` as its unset-variable compatibility fallback.

Remove statements saying OAuth and bearer/API credential modes are mutually
exclusive. Keep path-token authentication disabled in OAuth mode.

- [ ] **Step 3: Close the migration spike with sanitized evidence**

Update `docs/oauth-migration-spike.md` with only the status/boolean findings
recorded in the design spec: standard metadata absent; Auth0 discovery/JWKS
present; API credential not introspectable; exchanged JWT active; `client_id`
and `sub` absent; `account_id` and `user_id` present and stable across two fresh
exchanges; and the Task 0 FastMCP OAuth result (`client_id` present, `sub` and
`upstream_claims` absent). Select `MultiAuth(OpenbridgeOAuthProxy,
OpenbridgeCredentialVerifier)` with strict direct identity and isolated OAuth
session fallback, then link the design spec. Do not include tokens, account
identifiers, URLs containing credentials, or API response bodies.

- [ ] **Step 4: Run documentation/config tests**

Run:

```bash
.venv/bin/pytest tests/auth/test_authentication.py tests/auth/test_oauth_proxy.py tests/utils/test_runtime_security.py -v
```

Expected: all tests pass and the documented defaults match runtime behavior.

- [ ] **Step 5: Commit Task 5**

```bash
git add .env.example README.md AGENTS.md docker-compose.yml docs/oauth-migration-spike.md tests/auth/test_authentication.py tests/auth/test_oauth_proxy.py tests/utils/test_runtime_security.py
git commit -m "docs: describe OAuth and API credential flows" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 6: Verify the complete authentication migration

**Files:**
- Modify only if verification exposes a defect in files owned by Tasks 1-5.

**Interfaces:**
- Verifies every acceptance criterion in the design spec.

- [ ] **Step 1: Run the B1 regression request locally**

Start the server with auth enabled and stubbed Openbridge exchange/introspection
responses. POST an MCP tool call with `Authorization: Bearer bogus-token-xyz`.
Assert the response is HTTP `401` or the documented FastMCP authentication
error and does not contain `isError:false` with `result: []`.

- [ ] **Step 2: Run positive transport smoke tests**

With mocked upstreams, exercise OAuthProxy reference tokens, direct Openbridge
JWTs, and `xxx:yyy` API credentials through the HTTP MCP application. Assert
each reaches the spy tool exactly once and the tool sees the verified
Openbridge JWT through `get_access_token()`. Submit/list/get background tasks
under two normalized direct identities and two OAuth session-fallback
identities; assert no identity can observe another identity's task. Exercise
one nested Code Mode call and one enabled path-token request through the same
native context.

- [ ] **Step 3: Run the required repository checks in order**

Run exactly:

```bash
make lint
make check
make test
```

Expected: all three commands exit zero.

- [ ] **Step 4: Inspect the final diff for secret leakage and scope**

Run:

```bash
git diff --check
git diff --stat origin/main...HEAD
git grep -n "account123:api-secret\|sensitive upstream body" -- ':!tests/**' ':!docs/superpowers/plans/**'
```

Expected: no whitespace errors, only planned authentication/test/documentation
files changed, and no test sentinel appears in production or user-facing files.
Also inspect staged changes to ensure `.env` and `.env.prod` are absent.

- [ ] **Step 5: Commit any verification-only corrections**

If verification required code corrections, stage only the affected planned
files and commit:

```bash
git commit -m "fix: complete FastMCP auth migration" -m "🤖 Generated with [Claude Code](https://claude.com/claude-code)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

If no corrections were required, do not create an empty commit.
