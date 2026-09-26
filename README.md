# Openbridge MCP Server

The *Openbridge MCP Server* is a MCP server which enables LLMs to perform various tasks within the Openbridge platform.

## Quickstart

Get running in 3 steps: create a `.env`, start Docker, connect your AI client.

### 1. Create your `.env`

Copy the example, then choose OAuth or a direct Bearer credential. OAuth is the
recommended production and client flow.

```bash
cp .env.example .env
```

For OAuth, set the externally reachable MCP URL and a stable signing key:

```bash
MCP_PORT=8000
OPENBRIDGE_AUTH_MODE=oauth_proxy
MCP_BASE_URL=https://mcp.yourcompany.com
MCP_JWT_SIGNING_KEY=<output-of-openssl-rand-hex-32>
```

The unset `OPENBRIDGE_AUTH_MODE` compatibility default is `refresh_token`.
Despite its name, this mode accepts a caller-provided Openbridge JWT or
`account_id:token` API credential in the `Authorization: Bearer` header. FastMCP
validates either form before dispatching a tool.

For auth-disabled local development only, a loopback server may use a server
API credential:

```bash
MCP_HOST=127.0.0.1
AUTH_ENABLED=false
OPENBRIDGE_REFRESH_TOKEN=<your_account_id>:<your_token>
```

Docker containers must listen on `0.0.0.0`. A deliberately isolated,
single-tenant container that uses the server token therefore also requires
`OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true`. This escape hatch permits
unauthenticated requests to execute as the server principal; never use it for
a shared or publicly reachable application port.

### 2. Start the server with Docker

For **local development** with server-token fallback (no TLS, no Caddy — just
the MCP server and Redis), bind the published port to loopback and enable the
container compatibility escape hatch in `.env`:

```bash
docker compose up --build -d redis
OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true docker compose run -d -p 127.0.0.1:8000:8000 --name openbridge-mcp openbridge-mcp
```

Verify it's running:

```bash
docker compose logs -f openbridge-mcp
# Wait for "FastMCP server listening"
```

Your MCP endpoint is now at: `http://localhost:8000/mcp`

### 3. Connect your AI client

#### Claude Code (CLI)

```bash
claude mcp add --transport http openbridge http://localhost:8000/mcp
```

That's it — Claude Code will discover tools automatically on next launch.

#### Claude Desktop

Add this to your Claude Desktop config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "openbridge": {
      "command": "npx",
      "args": [
        "-y",
        "--allow-http",
        "mcp-remote@latest",
        "http://localhost:8000/mcp"
      ]
    }
  }
}
```

> In `refresh_token` mode, pass a direct Openbridge JWT or API credential:
> ```json
> {
>   "mcpServers": {
>     "openbridge": {
>       "command": "npx",
>       "args": [
>         "-y",
>         "--allow-http",
>         "mcp-remote@latest",
>         "http://localhost:8000/mcp",
>         "--header",
>         "Authorization:${AUTH_HEADER}"
>       ],
>       "env": {
>         "AUTH_HEADER": "Bearer <your_account_id>:<your_token>"
>       }
>     }
>   }
> }
> ```

#### Cursor / Windsurf / other MCP clients

Most MCP-compatible editors accept either:
- **HTTP URL:** `http://localhost:8000/mcp` (if the client supports HTTP transport)
- **npx bridge:** Same `npx mcp-remote@latest http://localhost:8000/mcp` pattern as Claude Desktop above

#### Remote deployment

For a remote server with TLS (e.g., behind Cloudflare or Caddy), replace `http://localhost:8000/mcp` with your public URL:

```
https://mcp.yourdomain.com/mcp
```

---

## Deployment

Local and remote deployments have different trust boundaries. Remote deployments
must use authenticated TLS ingress and keep the application port private; use the
bundled Caddy topology below or an equivalent authenticated reverse proxy.

### Docker deployment
1. Create a `.env` file at the project root with the variables listed below. The compose file mounts it into the container at `/app/.env`.
2. Build and start the stack: `docker compose up --build -d`
   - Caddy publishes ports 80 and 443 for ACME and TLS traffic. The application port is exposed only to the Compose network.
   - The stack also starts a small **Redis sidecar** that backs FastMCP's background-task queue (see *Topology* below).
3. Check logs with `docker compose logs -f openbridge-mcp` until you see “FastMCP server listening”.
4. Connect your MCP client to `https://${MCP_DOMAIN}/mcp`. Caddy redirects port 80 to TLS and is the only public entry point.

For Intel/AMD and ARM images, build both platforms with `docker buildx build --platform linux/amd64,linux/arm64 -t openbridgeops/openbridge-mcp:latest .`. Deploy the image behind TLS ingress with the application port private.

#### Topology

```
                ┌──────────────────────────┐
   client ──►   │ Caddy TLS ingress :443   │  (public)
                └──────┬───────────────────┘
                       │  compose-private HTTP
                       ▼
                ┌──────────────────────────┐
                │  openbridge-mcp:${PORT}  │  (not published)
                │  Code Mode + task worker │
                └──────┬───────────────────┘
                       │  redis://redis:6379/0
                       ▼  (compose-internal DNS)
                ┌──────────────────────────┐
                │  redis:7.4.5-alpine      │  (no published ports)
                │  AOF → redis-data volume │
                └──────────────────────────┘
```

The Redis sidecar is **only reachable from the openbridge-mcp container** — no `ports:` block is published to the host. The MCP container is the only ingress and egress for Redis traffic. Redis persists its append-only file to a named volume (`redis-data`) so the task queue survives container restarts. To wipe state for a clean dev re-run: `docker compose down -v`.

### Local deployment
As a prerequisite, we recommend using [**uv**](https://docs.astral.sh/uv/) to create and configure a virtual environment.

1. Create a `.env` in the project's root folder (see Variables below). At minimum set `MCP_PORT`. Auth-enabled clients must complete OAuth or provide a valid Bearer credential.
2. Run the command `uv venv --python 3.13 && uv pip install -e ".[dev]"`
3. Start the server:
   - Authenticated: `MCP_HOST=127.0.0.1 python main.py`
   - Debug without auth: `MCP_HOST=127.0.0.1 AUTH_ENABLED=false python main.py`
   - Remote binds fail at startup when authentication is disabled unless the dangerous compatibility override is explicit.
4. Connect from an MCP client.

### Environment variables (.env)
Required for server and tools to function. Values typically point to your environment (dev/stage/prod) of Openbridge APIs.

- Server
  - `MCP_PORT` (default `8000`): Internal HTTP port for the MCP server. Compose exposes it only to Caddy on the private network; ports 80 and 443 are the public ingress.
  - `MCP_HOST` (optional, default `0.0.0.0`): Host/interface to bind the MCP server. Use `127.0.0.1` for native local development; Compose explicitly uses `0.0.0.0` behind private TLS ingress.
  - `MCP_LIMIT_CONCURRENCY` (optional, default `100`): Maximum open Uvicorn connections; excess requests receive HTTP 503. Long-lived SSE streams count against the limit, so tune it from observed concurrent MCP sessions. Caddy rejects request bodies over 1 MB and suppresses request URIs from access logs. Per-IP or principal rate limits belong at the production ingress/WAF because the stock Caddy image has no rate-limit module.
  - `MCP_STATELESS_HTTP` (optional, default `true`): Run FastMCP's HTTP transport in stateless mode (a fresh transport per request). The default is safe for multi-instance deployments behind an L7 load balancer without sticky sessions. Set to `false` only if your deployment needs streamable HTTP session reuse and you can guarantee session affinity.
- Background tasks (SEP-2663)
  - `FASTMCP_DOCKET_URL` (default in compose: `redis://redis:6379/0`): Docket backend URL. The bundled Redis sidecar is reachable only on the compose-internal network; the openbridge-mcp container resolves `redis` via Docker DNS and is the only ingress to Redis. Use `memory://` for a single-process dev run with no compose file (tasks won't survive restart).
  - `FASTMCP_DOCKET_CONCURRENCY` (optional, default `10`): Maximum number of concurrent background tasks per worker. Tune for your Openbridge HTTP capacity.
  - `FASTMCP_TASKS_ENCRYPTION_KEY` (**required for Docker Compose**): Encrypts task context snapshots stored in Redis, including access tokens and HTTP headers. Generate a stable value with `openssl rand -hex 32`; every server and worker sharing the queue must use the same value.
- Code mode (primary client entry point)
  - `CODE_MODE` (default `true`): Code mode is the **recommended** client entry point — clients see only `tags`/`search`/`get_schema`/`execute` and use Python in a sandbox to call individual Openbridge tools. Setting `CODE_MODE=false` falls back to the direct tool catalog (every tool exposed by name) and emits a startup WARNING; only do this if you have a specific compatibility need.
- Logging
  - `LOG_LEVEL` (optional, default `INFO`): Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
  - `LOG_FORMAT` (optional, default `structured`): Log format (`structured` JSON or `simple` text).
- Authentication
  - `OPENBRIDGE_AUTH_MODE` (optional, compatibility default `refresh_token`): `oauth_proxy` is the recommended production/client mode. It provides browser OAuth and also accepts direct Openbridge JWT or `xxx:yyy` API-credential Bearer tokens. `refresh_token` keeps only the two direct Bearer paths for existing deployments.
  - The production `.env` selects `oauth_proxy`. Compose retains `refresh_token` only as its unset-variable compatibility fallback; set `OPENBRIDGE_AUTH_MODE=oauth_proxy` for production deployments.
  - OAuth credentials complete FastMCP's authorization flow and yield a Bearer token. Direct Openbridge JWTs are introspected. An `xxx:yyy` value is an Openbridge API credential, not an OAuth refresh token; the server exchanges it for a JWT and introspects that JWT before request dispatch.
  - Invalid or missing credentials fail at the FastMCP boundary. Successful introspection is cached for 30 seconds with a 256-entry cap. A downstream Openbridge `401` or `403` is still surfaced as an authentication failure.
  - `OPENBRIDGE_REFRESH_TOKEN` (optional): Server-side API credential used only when `AUTH_ENABLED=false` for local or isolated single-tenant fallback. Auth-enabled requests never assume this principal.
  - `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is deprecated and ignored. `AUTH_ENABLED=true` always requires a valid client credential.
  - `OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH` (optional, default `false`): Dangerous compatibility escape hatch permitting `AUTH_ENABLED=false` on a non-loopback bind. Use only when the application port is isolated for one trusted tenant; startup emits a prominent warning.
  - `OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY` (optional, default `8`): Positive worker limit for API-credential exchanges. Exchanges use a dedicated bounded executor, separate from FastMCP's sync-tool executor, and concurrent requests for the same credential share one in-flight operation.
  - `OPENBRIDGE_API_TIMEOUT` (optional, default `30`): Read timeout (seconds) applied to every Openbridge HTTP request; connect timeouts are fixed at 10 seconds.
  - `OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES` (optional, default `256`): Per-process LRU cap on API-credential → JWT exchange results. Raise this for deployments that serve more concurrent tenants than the default. Lower it to constrain memory in resource-tight environments.
- Authentication — deprecated URL-embedded auth / path tokens (`refresh_token` mode only)
  - `MCP_PATH_TOKEN_ENABLED` (optional, default `false`): Explicitly enables the legacy path-token middleware. Startup rejects this flag in `oauth_proxy` mode.
  - `MCP_PATH_TOKEN_SECRET` (required when enabled): Unique shared HS256 secret of at least 32 bytes. Placeholder values are rejected.
  - `MCP_PATH_TOKEN_TTL_DAYS` (optional, default `7`, maximum `7`): Lifetime for tokens generated by this server.
  - `MCP_PATH_TOKEN_MAX_AGE_DAYS` (optional, default `7`, maximum `7`): Maximum accepted token age and `exp - iat` lifetime.
- Authentication — OAuth Proxy Mode (`OPENBRIDGE_AUTH_MODE=oauth_proxy`)
  - `MCP_BASE_URL` (**required in production**): Externally-reachable base URL of this server, used by FastMCP to construct the OAuth redirect URI. Must match the URL your MCP clients and browsers use to reach the server. Example: `https://mcp.yourcompany.com`. Defaults to `http://{MCP_HOST}:{MCP_PORT}` — always override this when running behind a reverse proxy.
  - `MCP_JWT_SIGNING_KEY`: Stable secret used by FastMCP to sign session tokens. It is required when OAuth proxy mode binds beyond loopback, must contain at least 32 bytes, and cannot be a documented placeholder. Generate one with `openssl rand -hex 32`. Loopback development may omit it and use an ephemeral key.
  - `OPENBRIDGE_OAUTH_CLIENT_ID` (optional, default `openbridge-mcp`): Client ID sent to Openbridge's OAuth introspection endpoint. The endpoint reads credentials from embedded secrets, so this value is forwarded but typically not validated. Override only if explicitly required.
  - `OPENBRIDGE_OAUTH_CLIENT_SECRET` (optional, default `not-used`): Client secret for the introspection endpoint. Same semantics as `OPENBRIDGE_OAUTH_CLIENT_ID`.
  - `OPENBRIDGE_OAUTH_UPSTREAM_CLIENT_ID` (optional, default empty): Upstream `client_id` forwarded to `/auth/oauth/initialize`. Openbridge reads this from embedded secrets — leave empty unless instructed otherwise.
  - `AUTH0_*` variables do not configure this integration. OAuthProxy uses Openbridge's OAuth endpoints and the `OPENBRIDGE_OAUTH_*` settings above.
- Query Validation (AI-powered)
  - `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` (optional): A real key enables `validate_query` and is also required for `execute_query`. These tools call the OpenAI Responses API directly to validate SQL queries for read-only operations, LIMIT clauses, and related safety checks. Without a key, neither query tool is available. Get your API key at [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - `OPENBRIDGE_ENABLE_QUERY_EXECUTION` (optional, default `false`): Explicitly opts in to registering `execute_query`. Execution requires both this flag set to `true` and a real sampling API key; validation can remain available without execution.
  - `OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS` (optional, default `false`): Adds the credential-returning `get_amazon_api_access_token` tool and the five mutation tools (`update_history_status`, `create_job`, `create_subscription`, `update_subscription`, `cancel_subscription`). Enabling this profile makes these operations reachable through Code Mode's `execute` bridge, so use a trusted client and enforce upstream authorization.
  - `FASTMCP_SAMPLING_MODEL` (optional, default: `gpt-4o-mini`): OpenAI model to use for query validation.
  - `FASTMCP_SAMPLING_BASE_URL` (optional): Custom OpenAI-compatible API endpoint for query validation.
  - `OPENBRIDGE_ENABLE_LLM_VALIDATION` (optional, default `false`): Explicitly opt in to sending SQL text to the configured OpenAI-compatible endpoint for validation. When disabled the server uses heuristics only.
- Code Mode (default on)
  - `CODE_MODE` (optional, default `true`): Enables FastMCP Code Mode as the standard MCP surface.
  - `CODE_MODE_INCLUDE_TAGS` (optional, default `true`): Include `tags` discovery meta-tool in code mode.
  - `CODE_MODE_MAX_DURATION_SECS` (optional, default `30`): Sandbox execution timeout for `execute`.
  - `CODE_MODE_MAX_MEMORY` (optional, default `50000000`): Sandbox memory limit in bytes for `execute`.
  - Dependency note: Code mode requires `fastmcp[code-mode]` (includes sandbox dependencies such as pydantic-monty).

Example `.env` template (direct Bearer compatibility mode):
```bash
# Server settings
MCP_PORT=8000
# Native local development uses loopback
MCP_HOST=127.0.0.1

# Required for Docker Compose; encrypts task context persisted in Redis
# Generate with: openssl rand -hex 32
FASTMCP_TASKS_ENCRYPTION_KEY=

# Compatibility default: every request supplies its own Bearer credential.
# OPENBRIDGE_AUTH_MODE=refresh_token
# Optional timeout in seconds (connect timeout fixed at 10s)
OPENBRIDGE_API_TIMEOUT=45
# Auth-disabled local single-tenant fallback only:
# AUTH_ENABLED=false
# OPENBRIDGE_REFRESH_TOKEN=xxx:yyy
# Dangerous auth-disabled non-loopback container compatibility override:
# OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true
# Dedicated bounded API-credential exchange pool
OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY=8

# Deprecated URL-embedded auth is disabled by default
MCP_PATH_TOKEN_ENABLED=false
# Enabling requires a unique secret from: openssl rand -hex 32
# MCP_PATH_TOKEN_SECRET=
# MCP_PATH_TOKEN_TTL_DAYS=7
# MCP_PATH_TOKEN_MAX_AGE_DAYS=7

# Opt-in to AI validation; by default only heuristics run and no SQL leaves your environment
OPENBRIDGE_ENABLE_LLM_VALIDATION=false
# Explicit opt-in required for query execution tool registration
OPENBRIDGE_ENABLE_QUERY_EXECUTION=false
# Explicit opt-in for credential-returning and mutation tools
OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS=false

# A real key enables validate_query and is also required for execute_query
# FASTMCP_SAMPLING_API_KEY=
# or use OPENAI_API_KEY if you prefer
# OPENAI_API_KEY=

# Code mode (default true). Set false to expose full direct tool catalog.
CODE_MODE=true

# Optional logging controls
# LOG_LEVEL=INFO
# LOG_FORMAT=structured
```

Example `.env` template (oauth_proxy mode):
```bash
# Server settings
MCP_PORT=8000
MCP_HOST=0.0.0.0

# Authentication — OAuth Proxy mode
OPENBRIDGE_AUTH_MODE=oauth_proxy

# Externally-reachable URL used to construct the OAuth redirect URI.
# Must match the URL your clients use to reach this server.
MCP_BASE_URL=https://mcp.yourcompany.com

# Generate a stable signing key with: openssl rand -hex 32
MCP_JWT_SIGNING_KEY=

# Introspection credentials — defaults work for most Openbridge deployments
# OPENBRIDGE_OAUTH_CLIENT_ID=openbridge-mcp
# OPENBRIDGE_OAUTH_CLIENT_SECRET=not-used

# Optional logging controls
# LOG_LEVEL=INFO
# LOG_FORMAT=structured
```

### URL-Embedded Auth (Claude Custom Connectors, Deprecated)

Path tokens are disabled by default and remain a legacy compatibility mechanism
for `refresh_token` mode. They put a bearer-equivalent API credential in the
URL, where browsers, proxies, observability systems, and copied links can expose
it. Prefer OAuth proxy mode or an Authorization header. These controls reduce
exposure but do not close OB-MCP-02; replacing URL credentials with opaque,
revocable references requires a separate service design.

Claude custom connectors that cannot attach headers can use a pre-authenticated
connection URL issued by the Openbridge console:

```
https://mcp.yourcompany.com/mcp/{signed-jwt}
```

Paste the URL directly into Claude → Settings → Connectors → Add custom connector. Claude will use it for every subsequent request with no additional configuration.

When explicitly enabled, the server validates required `sub`, `iat`, `exp`, and
`aud` claims, enforces a seven-day maximum age and lifetime, rewrites the path to
`/mcp`, and injects the `Authorization` header. Sub-paths are preserved.

**Requirements on the server side:**

- Set `MCP_PATH_TOKEN_ENABLED=true` with `OPENBRIDGE_AUTH_MODE=refresh_token`.
- Set `MCP_PATH_TOKEN_SECRET` to a unique secret of at least 32 bytes (`openssl rand -hex 32`). It must match the console issuer; missing, short, and repository-placeholder values fail startup.
- Tokens must have `iat` and `exp`, contain an API-credential-shaped `sub`, and have both age and declared lifetime no greater than seven days. The former 30-day profile is no longer accepted, so console-issued URLs older than seven days must be replaced.
- The checked production profile uses `oauth_proxy` without a path-token secret, so this migration has no active production path URLs to rotate. Coordinate any other issuer before enabling this compatibility mode.
- Caddy URI logging is removed by the ingress-hardening task in this release. URL credentials may still leak outside Caddy, so treat every connection URL as a secret.

### Client configuration (example)
Once deployed, the Openbridge MCP can be utilized by any LLM with MCP support. Below is a sample configuration for use with Claude Desktop, assuming `MCP_PORT=8000` in your `.env` file.

```json
{
  "mcpServers": {
    "openbridge": {
      "command": "npx",
      "args": [
        "-y",
        "--allow-http",
        "mcp-remote@latest",
        "http://localhost:8000/mcp",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <YOUR_OB_TOKEN>"
      }
    }
  }
}
```

For more information about getting connected with Claude Desktop, visit the [**modelcontextprotocol** official documentation](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

### Skills (resources)
The server auto-loads the repo-bundled skill at `skills/openbridge-mcp/`. Clients can discover it through the MCP **resource** API — not tools or prompts.

Available URIs:
- `skill://openbridge-mcp/SKILL.md` — main instruction file with usage workflows
- `skill://openbridge-mcp/_manifest` — JSON listing every file in the skill bundle
- `skill://openbridge-mcp/references/<workflows|code-mode|error-envelope|embed-cli|tools-catalog>.md`
- `skill://openbridge-mcp/evals/evals.json` — eval fixtures

The provider uses `supporting_files="resources"` so reference docs and eval JSON are individually addressable. Reload is off; skills snapshot at image build time. See [AGENTS.md §Skills](AGENTS.md#skills) for the full directory contract and how to add new skills.

### Tools exposed
By default (`CODE_MODE=true`), Code Mode is active and clients typically see meta-tools like `search`, `get_schema`/`get_schemas`, and `execute` (plus `tags` when enabled).  
Set `CODE_MODE=false` to opt out and expose the direct tool catalog documented below.

The default catalog is read-oriented. Credential-returning and mutating tools are absent unless `OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS=true`. Call `get_capabilities` before planning a privileged workflow. Existing deployments that rely on these six tools must set the flag before deploying this release and confirm their presence in a smoke test.

- Capabilities
  - `get_capabilities`
    - Returns currently enabled and disabled tools, required environment variables, and opt-in behavior for query validation.
    - Example LLM request: `Show current MCP capabilities and why any tools are disabled`

- Remote identity - see [our documentation](https://docs.openbridge.com/en/articles/3673866-understanding-remote-identities) for more information.
  - `get_remote_identities`
    - Lists every remote identity linked to the current token, with an optional `remote_identity_type` filter if you only need one integration.
    - Example LLM request: `List my remote identities`
  - `get_remote_identity_by_id`
    - Retrieves a single remote identity by ID and flattens the nested `attributes` into top-level keys for easier prompting.
    - Example LLM request: `Fetch remote identity 12345 and show the flattened attributes`

- Query (heuristics by default, optional LLM validation)
  - **Availability**:
    - `validate_query` requires `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY`.
    - `execute_query` requires the same key plus `OPENBRIDGE_ENABLE_QUERY_EXECUTION=true`.
  - **Opt-in behavior**: Set `OPENBRIDGE_ENABLE_LLM_VALIDATION=true` to allow SQL text to be sent to the configured OpenAI-compatible endpoint. By default (`false`), validation is heuristic-only and SQL is not sent to an LLM.
  - `validate_query`
    - Validates SQL safety and best practices. Uses heuristics in default mode and optional LLM analysis when explicitly enabled. Pass `allow_unbounded=True` to permit queries without `LIMIT` clauses.
    - Example LLM request: `Validate this SQL against key finance and confirm it has a LIMIT 25`
  - `execute_query`
    - First validates SQL, then executes it through the Openbridge Service API. Requires authentication plus query tool availability. Override validation safeguards with `allow_unbounded=True` only when you intend to run queries without a `LIMIT`.
    - Example LLM request: `Execute the validated SQL on key merchandising with LIMIT 100`

- Rules - see [our data catalog documentation](https://docs.openbridge.com/en/articles/2247373-data-catalog-how-we-organize-and-manage-data-in-your-data-lake-or-cloud-warehouse) for more information.
  - `get_suggested_table_names`
    - Searches the Rules API (via the Service API) and returns structured candidates (`lookup_key`, aliases, destination table, rules path, confidence). Empty/no-match returns a v1 `TABLE_NOT_FOUND` envelope with recovery hints.
    - Example LLM request: `Suggest the best table names for a query about sponsored product spend`
  - `get_table_schema`
    - Fetches rules for a table and resolves alias variants (`bare`, `_master`, `_vNN`) to a canonical lookup key. Success returns normalized schema metadata; misses return a v1 `TABLE_NOT_FOUND` envelope with fuzzy suggestions.
    - Example LLM request: `Show the rules for table retail_orders_master`

- Service
  - `get_amazon_api_access_token`
    - Exchanges the remote identity for an Amazon Advertising API access token and its client ID so downstream calls can authenticate.
    - Example LLM request: `Retrieve the Amazon Advertising access token for remote identity 42`
  - `get_amazon_advertising_profiles`
    - Uses the Amazon token to enumerate available advertising profiles, inferring the region from the remote identity metadata.
    - Example LLM request: `List Amazon Advertising profiles for remote identity 42`

- Healthchecks - see [our documentation about healthchecks](https://docs.openbridge.com/en/articles/6906772-how-to-use-healthchecks) for more information.
  - `get_healthchecks`
    - Lists healthchecks for the current account with optional subscription and date filters, returning pagination info alongside the results.
    - Example LLM request: `List healthchecks for subscription 555 after 2024-01-01`

- Jobs
  - `get_jobs`
    - Returns jobs scoped to a subscription with optional status and primary flags so you can inspect running or historical syncs.
    - Example LLM request: `List active primary jobs for subscription 987`
  - `get_job_by_id`
    - Returns a single job by ID for targeted status or diagnostics.
    - Example LLM request: `Fetch job 123456`
  - `get_history_by_id`
    - Returns a single history transaction by history ID.
    - Example LLM request: `Fetch history transaction 424242`
  - `update_history_status`
    - Updates a history transaction status value.
    - Example LLM request: `Set history transaction 424242 status to cancelled`
  - `create_job`
    - Schedules one-off (historical) jobs for the subscription using ISO date strings and stage IDs that you can source from `get_product_stage_ids`.
    - Example LLM request: `Create one-off jobs for subscription 987 from 2024-01-01 to 2024-01-07 using stage ids [12, 34]`

- Subscriptions
  - `get_subscriptions`
    - Lists all subscriptions for the current user with pagination support. Returns subscription details including product IDs, status, and metadata.
    - Example LLM request: `Show me all my subscriptions`
  - `get_subscription_by_id`
    - Retrieves one subscription by ID.
    - Example LLM request: `Show subscription 128853`
  - `create_subscription`
    - Creates a subscription with JSON:API attributes.
    - Example LLM request: `Create a subscription with these attributes: {...}`
  - `update_subscription`
    - Updates an existing subscription with JSON:API attributes.
    - Example LLM request: `Update subscription 128853 with these attributes: {...}`
  - `cancel_subscription`
    - Cancels a subscription by setting status to `cancelled`.
    - Example LLM request: `Cancel subscription 128853`
  - `get_storage_subscriptions`
    - Lists active storage subscriptions linked to the current account. Returns storage-specific subscription details.
    - Example LLM request: `List my storage subscriptions`

- Products & Table Discovery
  - **Interactive workflow**: Use `search_products` → `list_product_tables` → `get_table_schema` for guided table discovery
  - **Type convention**: numeric IDs are strict integers (`123`), not string values (`"123"`).
  - `search_products`
    - Search for Openbridge products by name (case-insensitive). Returns matching products with IDs for use with `list_product_tables`.
    - Example LLM request: `Find products matching "Amazon Ads Sponsored"`
  - `list_product_tables`
    - List tables for a product as a structured response (`{product_id, tables}`), merging payload-backed rows with rules-only discoverables when available. Optionally filter by `subscription_id` to respect stage_ids.
    - Example LLM request: `Show me all tables for product 50` or `List tables for product 50 subscription 128853`
  - `get_product_stage_ids`
    - Returns stage IDs for a product, applying sensible `stage_id__gte` filters so you can quickly feed the results into job creation.
    - Example LLM request: `Look up the stage ids for product ob-product-123`

## Usage Examples

The Openbridge MCP server enables natural, conversational interactions with your data infrastructure. Here are common workflows:

### Discovering Tables and Schemas

**Interactive 3-step workflow** for finding and exploring tables:

**Example conversation:**
```
You: "Show me tables for Amazon Advertising Sponsored Products"

MCP: Calls search_products("Amazon Advertising Sponsored Products")
     Returns: [
       {"id": 70, "name": "Amazon Advertising Insights", ...},
       {"id": 48, "name": "Amazon Ads - Sponsored Products", ...}
     ]

MCP: "I found these Amazon Advertising products:
     1. Amazon Advertising Insights (ID: 70)
     2. Amazon Ads - Sponsored Products (ID: 48)
     Which would you like to explore?"

You: "The Sponsored Products one"

MCP: Calls list_product_tables(product_id=48)
     Returns list of tables for Sponsored Products

MCP: "Here are the available tables for Amazon Ads - Sponsored Products:
     - amzn_ads_sp_campaigns
     - amzn_ads_sp_adgroups
     - amzn_ads_sp_keywords
     - amzn_ads_sp_search_terms
     Which table schema would you like to see?"

You: "Show me the campaigns table schema"

MCP: Calls get_table_schema("amzn_ads_sp_campaigns")
     Returns complete schema with field definitions, types, rules
```

**Best practices for product search:**
- Use **broad, general terms**: "Amazon Ads", "Google Analytics", "Facebook"
- Avoid overly specific queries: ❌ "Amazon Advertising Sponsored Products Display Ads API v3"
- Queries are fuzzy-matched and ranked by relevance
- Partial word matches work: "Sponsored" will find products containing that word

### Working with Subscriptions

**Filter tables by subscription:**
```
You: "What tables are enabled for subscription 128853?"

MCP: Calls list_product_tables(product_id=50, subscription_id=128853)
     Returns only tables enabled for this subscription based on stage_ids

MCP: "Subscription 128853 has these tables enabled:
     - amzn_ads_sb_campaigns (stage_id: 1004)
     - amzn_ads_sb_keywords (stage_id: 1006)
     ..."
```

### Creating Historical Jobs

**Multi-step job creation:**
```
You: "Create a historical job for subscription 987 from Jan 1-7, 2024"

MCP: Calls get_product_stage_ids(product_id=...) to get available stages
     Calls create_job(subscription_id=987,
                               date_start="2024-01-01",
                               date_end="2024-01-07",
                               stage_ids=[1004, 1005, ...])

MCP: "Created historical jobs for subscription 987 covering Jan 1-7, 2024"
```

### Monitoring and Health Checks

**Check subscription health:**
```
You: "Show me any errors for subscription 555 in the last week"

MCP: Calls get_healthchecks(subscription_id=555, filter_date="2024-01-15")
     Returns healthcheck errors

MCP: "Found 3 errors for subscription 555:
     - Job 12345 failed on 2024-01-16 (API rate limit)
     - ..."
```

### Query Validation (AI-Powered)

**Safe SQL execution with validation:**
```
You: "I want to run: SELECT * FROM orders_master WHERE date > '2024-01-01'"

MCP: Calls validate_query(query="SELECT * FROM orders_master WHERE...",
                          key_name="production_db")
     AI analyzes query for safety

MCP: "⚠️ Warning: Query lacks LIMIT clause and may return large result set.
     Recommendation: Add LIMIT 1000 or set allow_unbounded=True"

You: "Add LIMIT 100"

MCP: Calls execute_query(query="SELECT * FROM orders_master... LIMIT 100",
                         key_name="production_db")
     Returns results
```

## Notes

- Authentication
  - **OAuth**: the primary production/client flow. FastMCP proxies authorization through Openbridge and validates the resulting access token.
  - **Direct JWT**: clients may pass an Openbridge JWT as `Authorization: Bearer <token>`; FastMCP introspects it before dispatch.
  - **API credential**: clients may pass `xxx:yyy` in the same header; the server exchanges it, then introspects the JWT before dispatch.
  - Auth-enabled requests always require one of those client credentials. Missing, malformed, inactive, or rejected credentials fail at the boundary.
- Query validation (AI-powered)
  - The `validate_query` and `execute_query` tools always run heuristic validation when available.
  - `validate_query` is available when `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` is configured. `execute_query` additionally requires `OPENBRIDGE_ENABLE_QUERY_EXECUTION=true`.
  - LLM-assisted validation is opt-in only and requires `OPENBRIDGE_ENABLE_LLM_VALIDATION=true`.
  - With opt-in enabled, SQL text may be sent to your configured OpenAI-compatible endpoint.
  - The AI validation checks for: read-only operations, proper LIMIT clauses, suspicious patterns, and SQL injection risks.
  - Get your API key from the [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - Cost consideration: Query validation typically uses the `gpt-4o-mini` model (configurable via `FASTMCP_SAMPLING_MODEL`), which is cost-effective for this use case.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Native local development should bind `127.0.0.1`. Compose binds the application to `0.0.0.0` only inside its private network and publishes Caddy on ports 80/443.
- Per-client authentication
  - **Shared deployments**: keep `AUTH_ENABLED=true`; each request completes OAuth or supplies its own Openbridge JWT/API credential. The server never substitutes `OPENBRIDGE_REFRESH_TOKEN`.
  - **Migration**: `OPENBRIDGE_REQUIRE_CLIENT_AUTH` is deprecated and ignored. Remove it from deployment configuration; authenticated endpoints now always fail closed.
  - **Local single-tenant fallback**: set `MCP_HOST=127.0.0.1 AUTH_ENABLED=false` and `OPENBRIDGE_REFRESH_TOKEN=xxx:yyy` only for isolated development.
  - **Isolated container compatibility**: an auth-disabled container bound to `0.0.0.0` also requires `OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true`. Keep the application port private; startup emits a warning.
  - **Deprecated URL-embedded auth** (Claude custom connectors): disabled by default, limited to seven days, and available only in `refresh_token` mode. It still places a bearer-equivalent credential in the URL; see [URL-Embedded Auth](#url-embedded-auth-claude-custom-connectors-deprecated).
  - Layer additional client authentication (network isolation, mTLS proxies, signed client configs, OS-level ACLs) as appropriate for your trust model.
  - Rotate tokens regularly and monitor access logs to detect misuse.
