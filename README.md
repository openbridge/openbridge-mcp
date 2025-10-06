## Openbridge MCP Server

A FastMCP-compatible server exposing tools to work with Openbridge APIs (remote identities, subscriptions, jobs, healthchecks, products, and query services).

### Entrypoint
- Run via Python: `main.py`

### Quick start
1. Create a `.env` in this folder (see Variables below). At minimum set `MCP_PORT` and `OPENBRIDGE_REFRESH_TOKEN` plus API base URLs.
2. Install dependencies for your environment (this project expects Python 3.10+ and the packages used in `src/` such as `fastmcp`, `python-dotenv`, `requests`).
3. Start the server:
   - Python: `python main.py`
   - The server listens on `0.0.0.0:${MCP_PORT}` using HTTP transport.
4. Connect from an MCP client. Example for the `fastagent` `mcp-remote` client is shown below.

### Launch options
- `python main.py`
  - Uses `.env` at project root
  - Host: `0.0.0.0`
  - Transport: `http`
  - Port: `MCP_PORT` (default `8010`)

If you are using the included `fastagent.config.yaml` with `mcp-remote` that points to `http://localhost:8010/mcp`, set `MCP_PORT=8010` in `.env` so the URLs align.

### Environment variables (.env)
Required for server and tools to function. Values typically point to your environment (dev/stage/prod) of Openbridge APIs.

- Server
  - `MCP_PORT` (default `8010`): Port for the HTTP MCP server.

- Authentication
  - `OPENBRIDGE_REFRESH_TOKEN`: Refresh token or bearer token used to obtain/send Authorization headers.
  - `OPENBRIDGE_AUTH_BASE_URL` (default `https://authentication.api.openbridge.io`): Auth base for refresh endpoint.
  - `REFRESH_TOKEN_ENDPOINT` (optional): If set, overrides the refresh endpoint; otherwise `${OPENBRIDGE_AUTH_BASE_URL}/auth/api/ref`.
  - Advanced (optional, only if you enable full JWT validation via middleware patterns):
    - `AUTH_ENABLED` (default `false`)
    - `JWT_VALIDATION_ENABLED` (default `true`)
    - `REFRESH_TOKEN_ENABLED` (default `false`)
    - `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URI`, `JWT_PUBLIC_KEY`
    - `JWT_VERIFY_SIGNATURE` (default `true`), `JWT_VERIFY_ISS` (default `true`), `JWT_VERIFY_AUD` (default `true`)
    - `JWT_REQUIRED_CLAIMS` (comma-separated)

- API base URLs (required by tools)
  - `SERVICE_API_BASE_URL`: Base URL for the Service API (query, rules proxy)
  - `REMOTE_IDENTITY_API_BASE_URL`: Base URL for Remote Identity API
  - `HEALTHCHECKS_API_BASE_URL`: Base URL for Healthchecks API
  - `JOBS_API_BASE_URL`: Base URL for Jobs API
  - `HISTORY_API_BASE_URL`: Base URL for History API (creating one-off jobs)
  - `SUBSCRIPTIONS_API_BASE_URL`: Base URL for Subscriptions API
- `PRODUCT_API_BASE_URL`: Base URL for Product API

- Sampling (optional)
  - `FASTMCP_SAMPLING_API_KEY`: API key used for server-side FastMCP sampling fallback (falls back to `OPENAI_API_KEY`).
  - `FASTMCP_SAMPLING_MODEL` (default `gpt-4o-mini`): Model used when sampling from the MCP server.
  - `FASTMCP_SAMPLING_BASE_URL` (optional): Override base URL for OpenAI-compatible providers.

Example `.env` template:
```bash
# Server
MCP_PORT=8010

# Auth
OPENBRIDGE_REFRESH_TOKEN=xxx:yyy
OPENBRIDGE_AUTH_BASE_URL=https://authentication.api.openbridge.io

# APIs
SERVICE_API_BASE_URL=https://service.api.openbridge.io
REMOTE_IDENTITY_API_BASE_URL=https://remote-identity.api.openbridge.io
HEALTHCHECKS_API_BASE_URL=https://service.api.openbridge.io/service/healthchecks/production/healthchecks/account
JOBS_API_BASE_URL=https://service.api.openbridge.io/service/jobs
SUBSCRIPTIONS_API_BASE_URL=https://subscriptions.api.openbridge.io
HISTORY_API_BASE_URL=https://service.api.openbridge.io/service/history/production
PRODUCT_API_BASE_URL=https://service.api.openbridge.io/service/products/product
```

### Client configuration (example)
This repository includes `fastagent.config.yaml` to connect with `mcp-remote` over HTTP.

```yaml
mcp:
  servers:
    openbridge:
      command: npx
      args: ["-y", "--allow-http", "mcp-remote", "http://localhost:8010/mcp"]
```

- Start this MCP server with `MCP_PORT=8010`
- Then start your MCP-compatible client that reads this config

### Tools exposed
Below are the MCP tools registered by the server and their purpose/parameters. All calls use auth headers derived from `OPENBRIDGE_REFRESH_TOKEN`.

- Remote identity
  - `get_remote_identities(remote_identity_type_id: Optional[str]) -> List[dict]`
    - Returns remote identities for current user; optional type filter.
  - `get_remote_identity_by_id(remote_identity_id: str) -> dict`
    - Returns a specific remote identity; flattens `attributes` into top-level keys.

- Service / Query / Rules
  - `validate_query(query: str, key_name: str, allow_unbounded: bool = False) -> dict`
    - Uses FastMCP sampling plus heuristics to enforce read-only rules; denies queries without `LIMIT` unless `allow_unbounded=True`.
  - `execute_query(query: str, key_name: str, allow_unbounded: bool = False) -> List[dict]`
    - Executes SQL via Service API only after `validate_query` approves; pass `allow_unbounded=True` to intentionally run without a `LIMIT`.
  - `get_amazon_api_access_token(remote_identity_id: int) -> dict`
    - Retrieves Amazon Advertising API access token and client id for a remote identity.
  - `get_amazon_advertising_profiles(remote_identity_id: int) -> List[dict]`
    - Uses the access token to list Amazon Advertising profiles. Region is inferred from the remote identity.
  - `get_suggested_table_names(query: str) -> List[str] | str`
    - Searches Rules API (via Service) for matching rule paths; returns table names (with `_master` suffix). Includes strict usage guidance for allowed keys in the description.
  - `get_table_rules(tablename: str) -> Optional[dict]`
    - Fetches the rule document for a given table name; accepts names with or without `_master` suffix.

- Healthchecks
  - `get_healthchecks(subscription_id: Optional[str] = None, filter_date: Optional[str] = None) -> List[dict]`
    - Lists healthchecks for the current account; supports basic filtering and pagination.

- Jobs
  - `get_jobs(subscription_id: int, status: Optional[str] = 'active', is_primary: Optional[str] = 'true') -> List[dict]`
    - Lists jobs with filters; `subscription_id` is required.
  - `create_oneoff_jobs(subscription_id: int, date_start: str, date_end: str, stage_ids: List[int]) -> List[dict] | [{"errors": ...}]`
    - Creates history (one-off) jobs for the subscription. Start/end should be ISO strings; `stage_ids` can be sourced via `get_product_stage_ids`.

- Products
  - `get_product_stage_ids(product_id: Optional[str]) -> List[dict]`
    - Returns stage IDs for a product (filters `stage_id__gte` to likely ranges).

### Notes
- Authentication
  - If `OPENBRIDGE_REFRESH_TOKEN` looks like a refresh token (`xxx:yyy`), the server will attempt to exchange it for a JWT using `OPENBRIDGE_AUTH_BASE_URL` (or `REFRESH_TOKEN_ENDPOINT` if set). If the exchange fails, the raw token is used as Bearer.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Server binds to all interfaces (`0.0.0.0`). Ensure firewall/network rules allow your MCP client to reach `MCP_PORT`.
- Per-client authentication
  - The MCP server uses a single `OPENBRIDGE_REFRESH_TOKEN` for all tool calls; downstream access is shared across clients.
  - Deployers must layer their own client authentication (e.g., network isolation, mTLS proxies, signed client configs, or OS-level ACLs) to ensure only trusted agents can invoke the server.
  - Rotate tokens regularly and monitor access logs to detect misuse when multiple operators share the same deployment.
  - You can also plug FastMCP’s standard authentication providers directly into this server (JWT validation, OAuth proxy, WorkOS AuthKit, etc.) if you prefer first-class per-client auth at the MCP layer; choose the provider that aligns with your org’s identity stack.
