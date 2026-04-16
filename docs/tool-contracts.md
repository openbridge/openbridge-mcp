# Tool Contracts

Authoritative specification of the intended behavior of every public function exposed by the 8 target modules when things go wrong. Tests in `tests/` assert these contracts. If a test and this doc disagree, fix the test first, then confirm the doc reflects intent.

**Scope note:** This first cut covers only the modules targeted by the current test-coverage improvement effort. Follow-ups will extend coverage to remaining tool modules once the format is proven.

**How to read an entry:**
- **Trigger** is the observable condition.
- **Behavior** is what the function must do (return value / raised exception / log level).
- `⚠ Open question` marks a point where current behavior may be unintentional — flag for team review before encoding in tests.

---

## `src/server/tools/service.py`

### `validate_query(query, key_name, allow_unbounded=False, ctx=None) -> Dict[str, Any]`

Pre-execution SQL assessment. Mixes heuristics and optional LLM sampling.

| Trigger | Behavior |
|---|---|
| `ctx is None` | Raise `ValueError("Context is required for validate_query")`. |
| Neither `FASTMCP_SAMPLING_API_KEY` nor `OPENAI_API_KEY` set | Raise `ValueError("Sampling API key required: ...")`. |
| `OPENBRIDGE_ENABLE_LLM_VALIDATION=false` (default) | Return result with heuristic-only decision; `sampling.supported=False`. |
| LLM sampling returns non-JSON | Capture in `sampling.error`, set `sampling_allows=False`, still return a result dict. |
| LLM sampling raises | Capture exception in `sampling.error`, fall back to heuristic `read_only`, still return a result dict. |
| Query has mutating keyword | Included in `heuristics.warnings`; `decision.allowed` becomes False. |
| Query has no `LIMIT` and `allow_unbounded=False` | `decision.limit_ok=False`, `decision.allowed=False`. |
| Network error (never applicable — no HTTP call) | N/A. |

### `execute_query(query, key_name, allow_unbounded=False, ctx=None) -> List[dict]`

Runs validated SQL via the Query API. Fail-closed: must not reach the network unless validation allowed it.

| Trigger | Behavior |
|---|---|
| `ctx is None` | Raise `ValueError("Context is required for execute_query")`. |
| `validate_query` raises `ValueError` | Return `[{"error": "Query validation unavailable: <msg>", "validation": "unavailable"}]`. No HTTP call. |
| Validation decides `allowed=False` | Return `[{"error": "Query validation failed", "validation": <full validation dict>}]`. No HTTP call. |
| HTTP 200 with valid JSON | Return `response_payload["data"]` (list of rows). |
| HTTP 200 with non-JSON body | Return `[{"error": "Failed to parse query response", "status": 200, "details": "Response body was not valid JSON", "validation": <v>}]`. |
| Non-200 response | Return `[{"error": "Failed to execute query", "status": <code>, "details": <text>, "validation": <v>}]`. Warn-log. |
| `requests.post` raises (Timeout/ConnectionError) | Return `[{"error": "Query execution failed", "status": None, "details": <str(exc)>, "validation": <v>}]`. Warn-log. |

### `get_table_schema(table_name, ctx=None) -> Optional[dict]`

Look up a single table's rules via the Rules API.

| Trigger | Behavior |
|---|---|
| Input ends with `_master` | Strip suffix before search (caller convenience). |
| HTTP 200 with `data: []` | Return `None`, info-log with URL + body preview. |
| HTTP 200 with one match | Return the single rule object. |
| HTTP 200 with multiple matches, one endswith exact `table_name` | Narrow to endswith-matching rows, return first. |
| HTTP 200 with multiple matches, none endswith | Return `None`, warn-log (ambiguous match — caller should refine). |
| HTTP 200 non-JSON | Return `None`, info-log with body preview. |
| Non-200 | Return `None`, warn-log with status, URL, body preview. |
| `requests.get` raises | Return `None`, warn-log. |

### `get_suggested_table_names(query, ctx=None) -> List[str]`

Fuzzy table-name discovery via the Rules API.

| Trigger | Behavior |
|---|---|
| HTTP 200 with matches | Return `[<leaf>_master, ...]` where `<leaf>` is the last `/`-segment of `attributes.path`. |
| HTTP 200 no matches | Return `[]`, info-log with query + body preview. |
| HTTP 200 non-JSON | Return `[]`, warn-log. |
| Non-200 | Return `[]`, warn-log with status + body preview. |
| `requests.RequestException` (any network failure) | Return `[]`, warn-log. |

### `get_amazon_api_access_token(remote_identity_id, ctx=None) -> Dict[str, Any]`

Exchange a remote identity for an Amazon Ads API access token.

| Trigger | Behavior |
|---|---|
| HTTP 200 with `data.access_token` | Return `{"access_token": <str>, "client_id": <str>}`. |
| HTTP 200 with missing or null `access_token` | Return error shape: `{"error": "Amazon API access token missing from response", "status": 200, "details": <body>}`. Downstream callers MUST check for `error` key, not just `access_token`. |
| HTTP 200 non-JSON | Return `{"error": "Failed to retrieve Amazon API access token", "status": 200, "details": <text>}`. |
| Non-200 | Return `{"error": "Failed to retrieve Amazon API access token", "status": <code>, "details": <json or text>}`. |
| `requests.get` raises | Return `{"error": "Amazon API access token request failed", "status": None, "details": <str(exc)>}`. Warn-log. |

### `get_amazon_advertising_profiles(remote_identity_id, ctx=None) -> List[dict]`

List Amazon Advertising profiles for an identity. Chains two upstream calls.

| Trigger | Behavior |
|---|---|
| `get_remote_identity_by_id` returns error dict or falsy | Return `[]`, warn-log. |
| `get_amazon_api_access_token` returns error dict (including missing/null token) | Return `[]`, warn-log. |
| HTTP 200 on profiles API | Return the JSON body. |
| Non-200 on profiles API | Return `[]`, warn-log with status. |
| `requests.get` raises on profiles API | Return `[]`, warn-log. |
| Identity `region` not in `AMZADV_REGIONAL_BASE_URLS` | Return `[]`, warn-log. |

---

## `src/server/tools/remote_identity.py`

### `get_remote_identities(remote_identity_type_id=None, ctx=None) -> List[dict]`

Paginated list of identities.

| Trigger | Behavior |
|---|---|
| HTTP 200 with `data` | Append rows, follow `links.next` via `safe_pagination_url`, repeat until no next. |
| Non-200 on any page | **Partial-results contract:** return rows collected so far, break pagination, warn-log. |
| `safe_pagination_url` rejects next URL | Stop pagination, return collected rows. |
| `requests.get` raises | Return rows collected so far, warn-log. |

### `get_remote_identity_by_id(remote_identity_id, ctx=None) -> dict`

| Trigger | Behavior |
|---|---|
| HTTP 200 with `data.attributes` | Return flattened dict (attributes merged up, `attributes` key removed). |
| HTTP 200 with `data: {}` or missing `attributes` | Return `{"error": "Remote identity <id> not found."}`, warn-log. Never crash on malformed payload. |
| Non-200 | Return `{"error": "Remote identity <id> not found."}`, warn-log. |
| `requests.get` raises | Return `{"error": "Remote identity <id> lookup failed", "details": <str(exc)>}`, warn-log. |

---

## `src/server/tools/subscriptions.py`

### `get_subscriptions(status='active', ctx=None) -> List[dict]`

Paginated, capped at `SUBSCRIPTIONS_MAX_PAGES=10`.

| Trigger | Behavior |
|---|---|
| HTTP 200 | Collect `data`, follow pagination. |
| Non-200 on any page | **Fail-fast:** return `[]` (discarding partial results), error-log. Intentional divergence from `get_remote_identities` partial-results behavior — subscription lists are used as complete inventory by callers; partial results would be misleading. |
| Reached max pages | Return partial, warn-log. |
| `requests.get` raises | **Fail-fast:** return `[]`, error-log. Same reasoning as non-200 case. |

### `get_subscription_by_id(subscription_id, ctx=None) -> Optional[dict]`

| Trigger | Behavior |
|---|---|
| HTTP 200 with `data` truthy | Return `data`. |
| HTTP 200 with `data` None/missing | Return `None`, warn-log. |
| Non-200 | Return `None`, error-log. |
| `requests.get` raises | Return `None`, warn-log. |

### `create_subscription(attributes, ctx=None) -> Optional[dict]`

| Trigger | Behavior |
|---|---|
| HTTP 200 or 201 | Return `response.json()["data"]`. |
| Any other status | Return `None`, error-log. |
| Non-JSON 200/201 | Return `None`, warn-log. |
| `requests.post` raises | Return `None`, warn-log. |

### `update_subscription(subscription_id, attributes, ctx=None) -> Optional[dict]`

Identical contract to `create_subscription` but allowed statuses are 200/202.

### `cancel_subscription(subscription_id, ctx=None) -> Optional[dict]`

Thin wrapper around `update_subscription({"status": "cancelled"})`. Contract follows parent.

### `get_storage_subscriptions(ctx=None) -> List[dict]`

Chains storages list + per-storage `/spm` calls.

| Trigger | Behavior |
|---|---|
| Storages API 2xx, SPM calls all 2xx | Return joined result list. |
| Storages API non-2xx | Return `[]`, error-log with status. |
| Storages API raises (network) | Return `[]`, error-log. |
| Storages response missing `data` key | Treat as empty list — return `[]`, warn-log. |
| SPM call for storage N fails (non-2xx or raises) | **Best-effort:** skip that storage with a warn-log, continue processing remaining storages. Partial results returned. |
| SPM response missing `data` key | Treat that storage's SPM as empty — include storage in results with `storage_type='unknown'` and no SPM keys. |
| SPM response missing `attributes` key in items | Already defensive via `.get('attributes', {})`. OK. |
| `product.name` missing in SPM | Defaults to `storage_type='unknown'`. OK. |

---

## `src/server/tools/base.py`

### `get_auth_headers(ctx=None) -> Dict[str, str]`

Resolves auth header in priority order: ContextVar → ctx.get_state → ctx attrs → env refresh token.

| Trigger | Behavior |
|---|---|
| ContextVar `_jwt_var` set to non-empty string | Return `{"Authorization": "Bearer <jwt>"}`. |
| `ctx.get_state` returns awaitable (async FastMCP) | Discard, fall through to attrs. |
| `ctx.get_state` returns non-str | Discard, fall through. |
| `ctx` attributes have JWT | Return Bearer header. |
| No context JWT, no env refresh token | Return `{}`. |
| Env refresh token exchange raises `AuthenticationError(not available)` | Return `{}`, debug-log. |
| Env refresh token exchange raises other `AuthenticationError` | Re-raise with actionable message (auth URL, possible causes). |

### `safe_pagination_url(next_url, base_url) -> Optional[str]`

SSRF guard for pagination links.

| Trigger | Behavior |
|---|---|
| `next_url` is None or empty | Return `None`. |
| Relative URL that resolves within same host | Return absolute URL. |
| Resolved URL not `https://` | Return `None`, warn-log. |
| Resolved host ≠ `base_url` host | Return `None`, warn-log. |
| `urljoin` produces malformed URL | Caught by `validate_url` → return `None`. |

### `_get_context_jwt(ctx) -> Optional[str]`

Internal helper; contract documented for test clarity. Never raises.

---

## `src/auth/simple.py`

### `get_api_timeout() -> Tuple[int, int]`

| Trigger | Behavior |
|---|---|
| `OPENBRIDGE_API_TIMEOUT` unset | Return `(10, 30)`. |
| `OPENBRIDGE_API_TIMEOUT=<int>` | Return `(10, int)`. |
| `OPENBRIDGE_API_TIMEOUT=<non-int>` | Parse once at import; on `ValueError`, warn-log once and fall back to default `(10, 30)`. Subsequent calls return cached tuple — no re-parse, no repeated warnings. |

### `is_refresh_token(token) -> bool`

Heuristic; must never raise. `None` / empty / short / non-string-safe inputs all return `False`.

### `OpenbridgeAuth.get_jwt() -> str`

| Trigger | Behavior |
|---|---|
| `refresh_token` not set | Raise `AuthenticationError("OPENBRIDGE_REFRESH_TOKEN not available for JWT generation")`. |
| Cached token valid | Return cached. |
| Cached token expired (with 5-min buffer) | Refresh and return. |
| `_do_exchange` raises | Propagate. |

### `OpenbridgeAuth.exchange_token(refresh_token) -> str`

| Trigger | Behavior |
|---|---|
| Cached (by raw refresh token value) valid | Return cached. |
| Cache size > 32 after insert | Evict oldest (FIFO dict order). |
| `_do_exchange` raises | Propagate `AuthenticationError`. |

### `OpenbridgeAuth._do_exchange(refresh_token) -> str`

| Trigger | Behavior |
|---|---|
| `requests.post` raises | Raise `AuthenticationError("Openbridge auth request failed")`. |
| Non-200 response | Raise `AuthenticationError("Failed to convert refresh token to JWT: ...")` (via `raise_for_status`). |
| 200 non-JSON | Raise `AuthenticationError("Failed to convert refresh token to JWT: ...")`. |
| 200 JSON missing `data.attributes.token` | Raise `AuthenticationError("Openbridge auth response did not include a token")`. |

---

## `src/auth/authentication.py`

### `OpenbridgeAuthMiddleware.on_request(context, call_next)`

Middleware priority: client Authorization header → server env refresh token → no auth.

| Trigger | Behavior |
|---|---|
| `context.fastmcp_context is None` | Skip all auth work, still invoke `call_next`. |
| Authorization header missing or not `Bearer ` | Fall through to server token path. |
| Authorization `Bearer ` with empty/whitespace token | Fall through to server token path. |
| Client refresh token (`xxx:yyy` shape) | Exchange via `_auth.exchange_token`, use resulting JWT. |
| Client JWT (3-segment) | Use as-is. |
| Client token resolution raises | Warn-log, fall through to server token path. |
| Server refresh token present | Mint JWT via `_auth.get_jwt`. |
| Neither available | `set_request_jwt(None)`, continue. |
| JWT resolved | `set_request_jwt(jwt)` **AND** both FastMCP context state keys written. |
| `_set_context_state` awaitable | Awaited; attr fallback also set. |

### `_set_context_state(ctx, key, value)`

| Trigger | Behavior |
|---|---|
| `ctx` is None | Return silently. |
| `ctx.set_state` is callable and returns awaitable | Await. |
| `ctx.set_state` raises | Debug-log, fall through. |
| Always | `setattr(ctx, key, value)` after set_state attempt. |

### `create_openbridge_config() -> AuthConfig`

| Trigger | Behavior |
|---|---|
| `AUTH_ENABLED=false` (case-insensitive) | All sub-flags False. |
| Unset or any other value | All sub-flags True (default enabled). |

### `create_auth_middleware(config, jwt_middleware=False, auth_manager=None) -> List[Middleware]`

| Trigger | Behavior |
|---|---|
| `config.enabled=False` | Return `[]` (no middleware). |
| Enabled, no `auth_manager` provided | Use `get_auth()` singleton. |
| Enabled with provided manager | Use it. |

---

## `src/auth/session_state.py`

### `set_request_jwt(token)` / `get_request_jwt()`

| Trigger | Behavior |
|---|---|
| Default (never set) | `get_request_jwt()` returns `None`. |
| Concurrent tasks | Each task sees only its own set value (ContextVar semantics). **Must not leak across tasks.** |
| Nested calls within same task | Inner `set` visible after nested call returns (standard ContextVar, no token reset). |
| Never | Raises. |

---

## `src/server/code_mode.py`

### `_env_bool(name, default) -> bool`

| Trigger | Behavior |
|---|---|
| Env var unset | Return `default`. |
| Value (lowercased, stripped) in `{"0", "false", "no", "off"}` | Return `False`. |
| Anything else (including `""`, `"1"`, `"true"`, `"yes"`, `"on"`) | Return `True`. |

### `is_code_mode_enabled() -> bool`

| Trigger | Behavior |
|---|---|
| `CODE_MODE` unset | Return `True` (default on). |
| Per `_env_bool` rules | Return accordingly. |

### `create_code_mode_transform() -> CodeMode`

| Trigger | Behavior |
|---|---|
| `fastmcp.experimental.transforms.code_mode` not importable | Raise `ImportError` with install instruction. |
| `CODE_MODE_MAX_DURATION_SECS` non-numeric | Raise `ValueError` at startup (fail-fast on config error). Intentional. |
| `CODE_MODE_MAX_MEMORY` non-numeric | Raise `ValueError` at startup (fail-fast on config error). Intentional. |
| All env valid | Return configured `CodeMode` instance. |

---

## Resolved Dispositions

All 8 questions are resolved. Sections above reflect the chosen behavior (no more `⚠` flags). Implementation follow-up PR aligns code to contract before Phase 1 tests.

| # | Topic | Decision | Implementation |
|---|--------|----------|----------------|
| 1 | Network errors (`RequestException`) in tool calls | **Adopt:** catch at the tool boundary, warn-log, return module's empty/error shape. Applies to `execute_query`, `get_table_schema`, `get_amazon_api_access_token`, `get_amazon_advertising_profiles`, `get_remote_identities`, `get_remote_identity_by_id`, `get_subscription_by_id`, `create_subscription`, `update_subscription`, `get_storage_subscriptions`. `get_subscriptions` explicitly keeps fail-fast (see #4). | Part of this alignment PR |
| 2 | `get_remote_identity_by_id` missing `attributes` | **Adopt:** return not-found shape, never crash. | Part of this alignment PR |
| 3 | `get_storage_subscriptions` partial failure | **Adopt:** best-effort per storage. Skip failed storages with warn-log, return what succeeded. | Part of this alignment PR |
| 4 | `get_subscriptions` pagination non-200 | **Keep current fail-fast.** Intentional divergence from `get_remote_identities` — subscription lists are used as complete inventories; partial results would mislead callers. Documented in contract row. | No code change |
| 5 | `get_amazon_api_access_token` missing/null token | **Adopt:** return error shape with `error` key. Callers must check for `error`. | Part of this alignment PR |
| 6 | `get_table_schema` multiple matches, no exact-suffix | **Adopt:** return `None` (ambiguous match → refuse to guess). | Part of this alignment PR |
| 7 | `get_amazon_advertising_profiles` unknown region | **Adopt:** return `[]` with warn-log instead of `KeyError`. | Part of this alignment PR |
| 8 | `get_api_timeout` invalid env | **Adopt:** parse once at import, fall back to default with warn-log on `ValueError`. Cached tuple returned on every call. | Part of this alignment PR |

Any future drift from these contracts is a bug; tests in Phase 1 lock them in.

---

## Change Log

- Initial draft — covers 8 target modules, surfaces 8 open questions.
- Dispositions resolved — 7 of 8 questions adopted (defensive behavior), Q4 (`get_subscriptions` fail-fast) kept intentionally. All ⚠ flags replaced with final behavior. Implementation alignment PR follows.
