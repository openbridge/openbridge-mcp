## Openbridge MCP Server

The *Openbridge MCP Server* is a FastMCP server which enables LLMs to perform various tasks within the Openbridge platform. 

## Deployment

### Docker deployment
1. Create a `.env` file at the project root with the variables listed below. The compose file mounts it into the container at `/app/.env`.
2. Build and start the stack: `docker compose up --build -d openbridge-mcp`
   - The compose file maps `8010:8010`; update both the port mapping and `MCP_PORT` in `.env` if you need a different port.
3. Check logs with `docker compose logs -f openbridge-mcp` until you see “FastMCP server listening”.
4. Connect your MCP client to `http://localhost:8010/mcp` (or the port you chose).

If you prefer raw Docker commands, run `docker build -t openbridge-mcp .` and then start it with `docker run --env-file .env -p 8010:8010 --name openbridge-mcp openbridge-mcp`. Add `--restart unless-stopped` if you want it to survive host restarts.

### Local deployment
As a prerequisite, we recommend using [**uv**](https://docs.astral.sh/uv/) to create and configure a virtual environment.

1. Create a `.env` in the project's root folder (see Variables below). At minimum set `MCP_PORT` and `OPENBRIDGE_REFRESH_TOKEN`.
2. Run the command `uv venv --python 3.12.7 && uv pip install -r requirements.txt`
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

Example `.env` template:
```bash
# Server
MCP_PORT=8010

# Auth
OPENBRIDGE_REFRESH_TOKEN=xxx:yyy

# OpenAI key is required for sampling, used in 
# OPENAI_API_KEY=XXXXX
```

### Client configuration (example)
Once deployed, the Openbridge MCP can be utilized by any LLM with MCP support. Below is a sample configuration for use with Claude Desktop, assuming `MCP_PORT=8010` in your `.env` file.

```json
{
  "mcpServers": {
    "openbridge": {
      "command": "npx",
      "args": [
        "-y",
        "--allow-http",
        "mcp-remote@latest",
        "http://localhost:8010/mcp"
      ]
    }
  }
}
```

For more information about getting connected with Claude Desktop, visit the [**modelcontextprotocol** official documentation](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

### Tools exposed
- Remote identity
  - `get_remote_identities`
    - Lists every remote identity linked to the current token, with an optional `remote_identity_type` filter if you only need one integration.
    - Example LLM request: `List my remote identities`
  - `get_remote_identity_by_id`
    - Retrieves a single remote identity by ID and flattens the nested `attributes` into top-level keys for easier prompting.
    - Example LLM request: `Fetch remote identity 12345 and show the flattened attributes`

- Query
  - `validate_query`
    - Runs a safety check against the FastMCP sampling guardrails to confirm a query is read-only and contains a `LIMIT` unless you explicitly pass `allow_unbounded=True`.
    - Example LLM request: `Validate this SQL against key finance and confirm it has a LIMIT 25`
  - `execute_query`
    - Executes SQL through the Service API after validation succeeds; override the safeguard with `allow_unbounded=True` only when you intend to run without a `LIMIT`.
    - Example LLM request: `Execute the validated SQL on key merchandising with LIMIT 100`

- Rules
  - `get_suggested_table_names`
    - Searches the Rules API (via the Service API) for tables that match the intent of your SQL, returning `_master` suffixed table names plus usage guidance.
    - Example LLM request: `Suggest the best table names for a query about sponsored product spend`
  - `get_table_rules`
    - Fetches the rules document for a table, whether or not you provide the `_master` suffix, so you can confirm allowed filters and columns.
    - Example LLM request: `Show the rules for table retail_orders_master`

- Service
  - `get_amazon_api_access_token`
    - Exchanges the remote identity for an Amazon Advertising API access token and its client ID so downstream calls can authenticate.
    - Example LLM request: `Retrieve the Amazon Advertising access token for remote identity 42`
  - `get_amazon_advertising_profiles`
    - Uses the Amazon token to enumerate available advertising profiles, inferring the region from the remote identity metadata.
    - Example LLM request: `List Amazon Advertising profiles for remote identity 42`

- Healthchecks
  - `get_healthchecks`
    - Lists healthchecks for the current account with optional subscription and date filters, returning pagination info alongside the results.
    - Example LLM request: `List healthchecks for subscription 555 after 2024-01-01`

- Jobs
  - `get_jobs`
    - Returns jobs scoped to a subscription with optional status and primary flags so you can inspect running or historical syncs.
    - Example LLM request: `List active primary jobs for subscription 987`
  - `create_oneoff_jobs`
    - Schedules one-off (historical) jobs for the subscription using ISO date strings and stage IDs that you can source from `get_product_stage_ids`.
    - Example LLM request: `Create one-off jobs for subscription 987 from 2024-01-01 to 2024-01-07 using stage ids [12, 34]`

- Products
  - `get_product_stage_ids`
    - Returns stage IDs for a product, applying sensible `stage_id__gte` filters so you can quickly feed the results into job creation.
    - Example LLM request: `Look up the stage ids for product ob-product-123`

### Notes
- Authentication
  - The server will attempt to exchange the `OPENBRIDGE_REFRESH_TOKEN` for a JWT.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Server binds to all interfaces (`0.0.0.0`). Ensure firewall/network rules allow your MCP client to reach `MCP_PORT`.
- Per-client authentication
  - The MCP server uses a single `OPENBRIDGE_REFRESH_TOKEN` for all tool calls; downstream access is shared across clients.
  - Deployers must layer their own client authentication (e.g., network isolation, mTLS proxies, signed client configs, or OS-level ACLs) to ensure only trusted agents can invoke the server.
  - Rotate tokens regularly and monitor access logs to detect misuse when multiple operators share the same deployment.
  - You can also plug FastMCP’s standard authentication providers directly into this server (JWT validation, OAuth proxy, WorkOS AuthKit, etc.) if you prefer first-class per-client auth at the MCP layer; choose the provider that aligns with your org’s identity stack.
