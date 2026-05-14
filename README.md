# Openbridge MCP Server

The *Openbridge MCP Server* is a MCP server which enables LLMs to perform various tasks within the Openbridge platform. 

## Deployment
Detailed below are setup and configuration instructions for a local machine, but the same steps can be taken to deploy the MCP on a remote server hosted on [AWS Fargate/EC2](https://aws.amazon.com/fargate), [Google Cloud Plaform (GCP)](https://cloud.google.com/blog/topics/developers-practitioners/build-and-deploy-a-remote-mcp-server-to-google-cloud-run-in-under-10-minutes), or any other remote server technology that best fits your environment.

### Docker deployment
1. Create a `.env` file at the project root with the variables listed below. The compose file mounts it into the container at `/app/.env`.
2. Build and start the stack: `docker compose up --build -d`
   - The compose file maps `8000:8000`; update both the port mapping and `MCP_PORT` in `.env` if you need a different port.
   - The stack also starts a small **Redis sidecar** that backs FastMCP's background-task queue (see *Topology* below).
3. Check logs with `docker compose logs -f openbridge-mcp` until you see “FastMCP server listening”.
4. Connect your MCP client to your server. If you did this locally, the address would look like: `http://localhost:8000/mcp` (or the port you chose). Running this on a remote server on Cloudflare, the URL would look like `https://mcp-openbridge-mcp.6fdec1c7650b77137a09f6fa4f2c9ca8.workers.dev`.

If you need as Intel/AMD compatable environment, you can build for both like this: `docker buildx build --platform linux/amd64,linux/arm64 -t openbridgeops/openbridge-mcp:latest .` and then start it with `docker run --env-file .env -p 8000:8000 --name openbridge-mcp openbridge-mcp`. Add `--restart unless-stopped` if you want it to survive host restarts.

#### Topology

```
                ┌──────────────────────────┐
   client ──►   │  openbridge-mcp:${PORT}  │  (only public ingress)
                │  Code Mode meta-tools    │
                │  + FastMCP tasks worker  │
                └──────┬───────────────────┘
                       │  redis://redis:6379/0
                       ▼  (compose-internal DNS)
                ┌──────────────────────────┐
                │  redis:7-alpine          │  (no published ports)
                │  AOF → redis-data volume │
                └──────────────────────────┘
```

The Redis sidecar is **only reachable from the openbridge-mcp container** — no `ports:` block is published to the host. The MCP container is the only ingress and egress for Redis traffic. Redis persists its append-only file to a named volume (`redis-data`) so the task queue survives container restarts. To wipe state for a clean dev re-run: `docker compose down -v`.

### Local deployment
As a prerequisite, we recommend using [**uv**](https://docs.astral.sh/uv/) to create and configure a virtual environment.

1. Create a `.env` in the project's root folder (see Variables below). At minimum set `MCP_PORT`. Optionally set `OPENBRIDGE_REFRESH_TOKEN` for server-side authentication (clients can also provide tokens via Authorization headers).
2. Run the command `uv venv --python 3.13 && uv pip install -e ".[dev]"`
3. Start the server:
   - Python: `python main.py`
   - The server listens on `${MCP_HOST:-0.0.0.0}:${MCP_PORT}` using HTTP transport.
4. Connect from an MCP client.

### Environment variables (.env)
Required for server and tools to function. Values typically point to your environment (dev/stage/prod) of Openbridge APIs.

- Server
  - `MCP_PORT` (default `8000`): Port for the HTTP MCP server. The container exposes `8000` internally; `docker-compose.yml` publishes it as `${MCP_PORT:-8000}` on the host, so set `MCP_PORT` in `.env` if you need a different host port.
  - `MCP_HOST` (optional, default `0.0.0.0`): Host/interface to bind the MCP server.
  - `MCP_STATELESS_HTTP` (optional, default `true`): Run FastMCP's HTTP transport in stateless mode (a fresh transport per request). The default is safe for multi-instance deployments behind an L7 load balancer without sticky sessions. Set to `false` only if your deployment needs streamable HTTP session reuse and you can guarantee session affinity.
- Background tasks (SEP-1686)
  - `FASTMCP_DOCKET_URL` (default in compose: `redis://redis:6379/0`): Docket backend URL. The bundled Redis sidecar is reachable only on the compose-internal network; the openbridge-mcp container resolves `redis` via Docker DNS and is the only ingress to Redis. Use `memory://` for a single-process dev run with no compose file (tasks won't survive restart).
  - `FASTMCP_DOCKET_CONCURRENCY` (optional, default `10`): Maximum number of concurrent background tasks per worker. Tune for your Openbridge HTTP capacity.
- Code mode (primary client entry point)
  - `CODE_MODE` (default `true`): Code mode is the **recommended** client entry point — clients see only `tags`/`search`/`get_schema`/`execute` and use Python in a sandbox to call individual Openbridge tools. Setting `CODE_MODE=false` falls back to the direct tool catalog (every tool exposed by name) and emits a startup WARNING; only do this if you have a specific compatibility need.
- Logging
  - `LOG_LEVEL` (optional, default `INFO`): Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
  - `LOG_FORMAT` (optional, default `structured`): Log format (`structured` JSON or `simple` text).
- Authentication
  - `OPENBRIDGE_AUTH_MODE` (optional, default `refresh_token`): Selects the authentication mode. Two mutually exclusive options:
    - `refresh_token` (default): `OpenbridgeAuthMiddleware` extracts `Authorization: Bearer` headers, exchanges Openbridge refresh tokens (`xxx:yyy`) for short-lived JWTs, and caches results per-tenant. See below for single- vs multi-tenant config.
    - `oauth_proxy`: FastMCP's built-in `OAuthProxy` takes over. The server advertises OAuth 2.0 metadata, proxies the authorization code flow to Openbridge's auth service, and verifies access tokens via token introspection. MCP clients authenticate through a browser-based OAuth flow rather than passing raw tokens. See [OAuth Proxy Mode](#oauth-proxy-mode) below.
  - `OPENBRIDGE_REFRESH_TOKEN` (optional): Refresh token for server-side authentication (`refresh_token` mode only). When set, the server exchanges this for JWTs to authenticate API calls. When unset, clients must provide Bearer tokens via `Authorization` headers. If neither is provided, API calls will fail with `401`.
  - `OPENBRIDGE_REQUIRE_CLIENT_AUTH` (optional, default `false`): **Required for multi-tenant deployments** (`refresh_token` mode). When `true`, requests that arrive without an `Authorization: Bearer` header are rejected with `McpError(-32001)` instead of falling back to `OPENBRIDGE_REFRESH_TOKEN`. Prevents cross-tenant data leakage by ensuring every tool call runs under the caller's own credential. Leave `false` for single-tenant or local-dev installs. Not applicable in `oauth_proxy` mode — the OAuthProxy enforces authentication at the transport layer.
  - `OPENBRIDGE_API_TIMEOUT` (optional, default `30`): Read timeout (seconds) applied to every Openbridge HTTP request; connect timeouts are fixed at 10 seconds.
  - `OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES` (optional, default `256`): Per-process LRU cap on cached client refresh-token → JWT mappings (`refresh_token` mode only). Raise this for deployments that serve more concurrent tenants than the default. Lower it to constrain memory in resource-tight environments. Eviction is LRU, so active tenants stay resident under churn.
- Authentication — URL-Embedded Auth / Path Tokens (`refresh_token` mode only)
  - `MCP_PATH_TOKEN_SECRET` (**required for stable production tokens**): Shared HS256 signing secret used to verify JWTs embedded in connection URLs (see [URL-Embedded Auth](#url-embedded-auth-claude-custom-connectors)). Any service that knows this secret can generate valid connection URLs. If unset, an ephemeral secret is generated on startup and all path tokens are invalidated on restart. Use `openssl rand -hex 32` to generate a suitable value.
  - `MCP_PATH_TOKEN_TTL_DAYS` (optional, default `30`): Lifetime in days for path tokens generated by `build_connection_url` or `PathTokenMiddleware.generate_token`. Tokens from external services are accepted until their own `exp` claim expires.
- Authentication — OAuth Proxy Mode (`OPENBRIDGE_AUTH_MODE=oauth_proxy`)
  - `MCP_BASE_URL` (**required in production**): Externally-reachable base URL of this server, used by FastMCP to construct the OAuth redirect URI. Must match the URL your MCP clients and browsers use to reach the server. Example: `https://mcp.yourcompany.com`. Defaults to `http://{MCP_HOST}:{MCP_PORT}` — always override this when running behind a reverse proxy.
  - `MCP_JWT_SIGNING_KEY` (recommended): Stable secret used by FastMCP to sign the session tokens it issues to MCP clients. If unset, a random key is generated per process start and all active MCP sessions break on server restart. Set to any strong, stable secret for production deployments.
  - `OPENBRIDGE_OAUTH_CLIENT_ID` (optional, default `openbridge-mcp`): Client ID sent to Openbridge's OAuth introspection endpoint. The endpoint reads credentials from embedded secrets, so this value is forwarded but typically not validated. Override only if explicitly required.
  - `OPENBRIDGE_OAUTH_CLIENT_SECRET` (optional, default `not-used`): Client secret for the introspection endpoint. Same semantics as `OPENBRIDGE_OAUTH_CLIENT_ID`.
  - `OPENBRIDGE_OAUTH_UPSTREAM_CLIENT_ID` (optional, default empty): Upstream `client_id` forwarded to `/auth/oauth/initialize`. Openbridge reads this from embedded secrets — leave empty unless instructed otherwise.
- Query Validation (AI-powered)
  - `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` (optional): Required to enable the `validate_query` and `execute_query` tools. These tools use AI-powered sampling to validate SQL queries and ensure they follow best practices (read-only operations, proper LIMIT clauses, etc.). Without this key, query validation tools will not be available. Get your API key at [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - `OPENBRIDGE_ENABLE_QUERY_EXECUTION` (optional, default `true`): Controls registration of the `execute_query` tool independently of `validate_query`. Set to `false` to keep validation-only mode enabled.
  - `FASTMCP_SAMPLING_MODEL` (optional, default: `gpt-4o-mini`): OpenAI model to use for query validation.
  - `FASTMCP_SAMPLING_BASE_URL` (optional): Custom OpenAI-compatible API endpoint for query validation.
  - `OPENBRIDGE_ENABLE_LLM_VALIDATION` (optional, default `false`): Explicitly opt in to sending SQL text to the configured OpenAI-compatible endpoint for validation. When disabled the server uses heuristics only.
- Code Mode (default on)
  - `CODE_MODE` (optional, default `true`): Enables FastMCP Code Mode as the standard MCP surface.
  - `CODE_MODE_INCLUDE_TAGS` (optional, default `true`): Include `tags` discovery meta-tool in code mode.
  - `CODE_MODE_MAX_DURATION_SECS` (optional, default `30`): Sandbox execution timeout for `execute`.
  - `CODE_MODE_MAX_MEMORY` (optional, default `50000000`): Sandbox memory limit in bytes for `execute`.
  - Dependency note: Code mode requires `fastmcp[code-mode]` (includes sandbox dependencies such as pydantic-monty).

Example `.env` template (refresh_token mode — the default):
```bash
# Server settings
MCP_PORT=8000
# MCP_HOST=0.0.0.0

# Authentication — refresh_token mode (default)
OPENBRIDGE_REFRESH_TOKEN=xxx:yyy
# Optional timeout in seconds (connect timeout fixed at 10s)
OPENBRIDGE_API_TIMEOUT=45
# Required for multi-tenant deployments; rejects requests without a Bearer header
# OPENBRIDGE_REQUIRE_CLIENT_AUTH=true

# URL-Embedded Auth (Claude custom connectors) — shared HS256 secret for path tokens
# Generate with: openssl rand -hex 32
MCP_PATH_TOKEN_SECRET=your-32-byte-hex-secret-here
# MCP_PATH_TOKEN_TTL_DAYS=30

# Opt-in to AI validation; by default only heuristics run and no SQL leaves your environment
OPENBRIDGE_ENABLE_LLM_VALIDATION=false
# Optional hard gate for query execution tool registration
OPENBRIDGE_ENABLE_QUERY_EXECUTION=true

# Query validation (AI-powered) - required for validate_query and execute_query tools
FASTMCP_SAMPLING_API_KEY=sk-proj-xxxxxxxxxxxxx
# or use OPENAI_API_KEY if you prefer
# OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx

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

# Stable signing key for FastMCP session tokens — sessions break on
# restart if this is not set. Use any strong, stable secret.
MCP_JWT_SIGNING_KEY=your-strong-stable-secret-here

# Introspection credentials — defaults work for most Openbridge deployments
# OPENBRIDGE_OAUTH_CLIENT_ID=openbridge-mcp
# OPENBRIDGE_OAUTH_CLIENT_SECRET=not-used

# Optional logging controls
# LOG_LEVEL=INFO
# LOG_FORMAT=structured
```

### URL-Embedded Auth (Claude Custom Connectors)

Claude's custom connector UI accepts a single URL with no way to attach custom HTTP headers. To support this, the Openbridge platform can issue per-user **pre-authenticated connection URLs** from the Openbridge console. These URLs embed a signed JWT in the path:

```
https://mcp.yourcompany.com/mcp/{signed-jwt}
```

Paste the URL directly into Claude → Settings → Connectors → Add custom connector. Claude will use it for every subsequent request with no additional configuration.

The server validates the JWT (signature, expiry, and audience) on every request, rewrites the path to `/mcp`, and injects the appropriate `Authorization` header transparently. Sub-paths are preserved — streaming and SSE endpoints work without a separate token.

**Requirements on the server side:**

- Set `MCP_PATH_TOKEN_SECRET` to a stable 32-byte secret (use `openssl rand -hex 32`). The Openbridge console signs tokens with this same secret, so the values must match. If the variable is unset, an ephemeral secret is generated on startup and all existing connection URLs stop working on the next restart.
- Tokens expire after `MCP_PATH_TOKEN_TTL_DAYS` days (default 30). Users will need a new URL from the console after expiry.

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

### Tools exposed
By default (`CODE_MODE=true`), FastMCP Code Mode is active and clients typically see meta-tools like `search`, `get_schema`/`get_schemas`, and `execute` (plus `tags` when enabled).  
Set `CODE_MODE=false` to opt out and expose the direct tool catalog documented below.

See [docs/tool-coverage-matrix.md](docs/tool-coverage-matrix.md) for endpoint-family coverage status and planned phases.

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

- Authentication (Dual-Mode)
  - **Server-side auth**: Set `OPENBRIDGE_REFRESH_TOKEN` in the server's environment. The server automatically exchanges it for JWTs.
  - **Client-side auth**: Clients pass `Authorization: Bearer <token>` headers. The server uses the client-provided token directly.
  - **Priority**: Client-provided tokens take precedence over server tokens. If neither is provided, API calls fail with `401`.
  - The server starts successfully even without `OPENBRIDGE_REFRESH_TOKEN`, enabling pure client-side authentication deployments.
- Query validation (AI-powered)
  - The `validate_query` and `execute_query` tools always run heuristic validation when available.
  - These tools are only available when `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` is configured in your environment.
  - LLM-assisted validation is opt-in only and requires `OPENBRIDGE_ENABLE_LLM_VALIDATION=true`.
  - With opt-in enabled, SQL text may be sent to your configured OpenAI-compatible endpoint.
  - The AI validation checks for: read-only operations, proper LIMIT clauses, suspicious patterns, and SQL injection risks.
  - Get your API key from the [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - Cost consideration: Query validation typically uses the `gpt-4o-mini` model (configurable via `FASTMCP_SAMPLING_MODEL`), which is cost-effective for this use case.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Server binds to all interfaces (`0.0.0.0`). Ensure firewall/network rules allow your MCP client to reach `MCP_PORT`.
- Per-client authentication
  - **Single-tenant** (one operator, one Openbridge account): `OPENBRIDGE_REFRESH_TOKEN` alone is sufficient. The server exchanges it for short-lived JWTs and uses the same principal for every tool call.
  - **Multi-tenant** (one shared server instance, many distinct Openbridge accounts): set `OPENBRIDGE_REQUIRE_CLIENT_AUTH=true` so the server rejects any request lacking an `Authorization: Bearer` header. Each client must send its own refresh token (`xxx:yyy`) or unexpired JWT in that header; the server resolves it on a per-request basis and never substitutes the server-side token. Without this flag, an unauthenticated request would silently execute as the server's principal — a cross-tenant data leak.
  - **URL-embedded auth** (Claude custom connectors): The Openbridge console issues per-user connection URLs with a signed JWT in the path. No header configuration is needed — the server extracts and verifies the token before routing. Requires `MCP_PATH_TOKEN_SECRET` to match the secret used by the console. See [URL-Embedded Auth](#url-embedded-auth-claude-custom-connectors).
  - Layer additional client authentication (network isolation, mTLS proxies, signed client configs, OS-level ACLs) as appropriate for your trust model.
  - Rotate tokens regularly and monitor access logs to detect misuse.
  - You can also plug FastMCP's standard authentication providers directly into this server (JWT validation, OAuth proxy, WorkOS AuthKit, etc.) if you prefer first-class per-client auth at the MCP layer; choose the provider that aligns with your org's identity stack.
