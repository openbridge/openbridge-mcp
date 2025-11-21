# Openbridge MCP Server

The *Openbridge MCP Server* is a MCP server which enables LLMs to perform various tasks within the Openbridge platform. 

## Deployment
Detailed below are setup and configuration instructions for a local machine, but the same steps can be taken to deploy the MCP on a remote server hosted on [AWS Fargate/EC2](https://aws.amazon.com/fargate), [Google Cloud Plaform (GCP)](https://cloud.google.com/blog/topics/developers-practitioners/build-and-deploy-a-remote-mcp-server-to-google-cloud-run-in-under-10-minutes), or any other remote server technology that best fits your environment.

### Docker deployment
1. Create a `.env` file at the project root with the variables listed below. The compose file mounts it into the container at `/app/.env`.
2. Build and start the stack: `docker compose up --build -d openbridge-mcp`
   - The compose file maps `8000:8000`; update both the port mapping and `MCP_PORT` in `.env` if you need a different port.
3. Check logs with `docker compose logs -f openbridge-mcp` until you see “FastMCP server listening”.
4. Connect your MCP client to your server. If you did this locally, the address would look like: `http://localhost:8000/mcp` (or the port you chose). Running this on a remote server on Cloudflare, the URL would look like `https://mcp-openbridge-mcp.6fdec1c7650b77137a09f6fa4f2c9ca8.workers.dev`.

If you need as Intel/AMD compatable environment, you can build for both like this: `docker buildx build --platform linux/amd64,linux/arm64 -t openbridgeops/openbridge-mcp:latest .` and then start it with `docker run --env-file .env -p 8000:8000 --name openbridge-mcp openbridge-mcp`. Add `--restart unless-stopped` if you want it to survive host restarts.

### Local deployment
As a prerequisite, we recommend using [**uv**](https://docs.astral.sh/uv/) to create and configure a virtual environment.

1. Create a `.env` in the project's root folder (see Variables below). At minimum set `MCP_PORT`. Optionally set `OPENBRIDGE_REFRESH_TOKEN` for server-side authentication (clients can also provide tokens via Authorization headers).
2. Run the command `uv venv --python 3.12.7 && uv pip install -r requirements.txt`
3. Start the server:
   - Python: `python main.py`
   - The server listens on `0.0.0.0:${MCP_PORT}` using HTTP transport.
4. Connect from an MCP client.

### Environment variables (.env)
Required for server and tools to function. Values typically point to your environment (dev/stage/prod) of Openbridge APIs.

- Server
  - `MCP_PORT` (default `8010`): Port for the HTTP MCP server.
- Authentication
  - `OPENBRIDGE_REFRESH_TOKEN` (optional): Refresh token for server-side authentication. When set, the server exchanges this for JWTs to authenticate API calls. When unset, clients must provide Bearer tokens via `Authorization` headers. If neither is provided, API calls will fail with `401`.
  - `OPENBRIDGE_API_TIMEOUT` (optional, default `30`): Read timeout (seconds) applied to every Openbridge HTTP request; connect timeouts are fixed at 10 seconds.
- Query Validation (AI-powered)
  - `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` (optional): Required to enable the `validate_query` and `execute_query` tools. These tools use AI-powered sampling to validate SQL queries and ensure they follow best practices (read-only operations, proper LIMIT clauses, etc.). Without this key, query validation tools will not be available. Get your API key at [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - `FASTMCP_SAMPLING_MODEL` (optional, default: `gpt-4o-mini`): OpenAI model to use for query validation.
  - `FASTMCP_SAMPLING_BASE_URL` (optional): Custom OpenAI-compatible API endpoint for query validation.
  - `OPENBRIDGE_ENABLE_LLM_VALIDATION` (optional, default `false`): Explicitly opt in to sending SQL text to the configured OpenAI-compatible endpoint for validation. When disabled the server uses heuristics only.

Example `.env` template:
```bash
# Server settings
MCP_PORT=8000

# Authentication settings
OPENBRIDGE_REFRESH_TOKEN=xxx:yyy
# Optional timeout in seconds (connect timeout fixed at 10s)
OPENBRIDGE_API_TIMEOUT=45
# Opt-in to AI validation; by default only heuristics run and no SQL leaves your environment
OPENBRIDGE_ENABLE_LLM_VALIDATION=false

# Query validation (AI-powered) - required for validate_query and execute_query tools
FASTMCP_SAMPLING_API_KEY=sk-proj-xxxxxxxxxxxxx
# or use OPENAI_API_KEY if you prefer
# OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx
```

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
- Remote identity - see [our documentation](https://docs.openbridge.com/en/articles/3673866-understanding-remote-identities) for more information.
  - `get_remote_identities`
    - Lists every remote identity linked to the current token, with an optional `remote_identity_type` filter if you only need one integration.
    - Example LLM request: `List my remote identities`
  - `get_remote_identity_by_id`
    - Retrieves a single remote identity by ID and flattens the nested `attributes` into top-level keys for easier prompting.
    - Example LLM request: `Fetch remote identity 12345 and show the flattened attributes`

- Query (AI-powered validation - requires OpenAI API key)
  - **Note**: Both query tools require `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` to be configured. These tools use AI-powered sampling to intelligently validate SQL queries and ensure best practices. See the [OpenAI Platform documentation](https://platform.openai.com/docs/api-reference/introduction) to obtain an API key. Without an API key configured, these tools will not be available.
  - `validate_query`
    - Uses AI-powered sampling to analyze SQL queries for safety and best practices. Confirms queries are read-only, contain proper `LIMIT` clauses, and follow security guidelines. Pass `allow_unbounded=True` to explicitly permit queries without `LIMIT` clauses.
    - Example LLM request: `Validate this SQL against key finance and confirm it has a LIMIT 25`
  - `execute_query`
    - First validates the SQL query using AI-powered sampling, then executes it through the Openbridge Service API. Requires both `OPENBRIDGE_REFRESH_TOKEN` and an OpenAI API key. Override validation safeguards with `allow_unbounded=True` only when you intend to run queries without a `LIMIT`.
    - Example LLM request: `Execute the validated SQL on key merchandising with LIMIT 100`

- Rules - see [our data catalog documentation](https://docs.openbridge.com/en/articles/2247373-data-catalog-how-we-organize-and-manage-data-in-your-data-lake-or-cloud-warehouse) for more information.
  - `get_suggested_table_names`
    - Searches the Rules API (via the Service API) for tables that match the intent of your SQL, returning `_master` suffixed table names plus usage guidance.
    - Example LLM request: `Suggest the best table names for a query about sponsored product spend`
  - `get_table_schema`
    - Fetches the rules document for a table, whether or not you provide the `_master` suffix, so you can confirm allowed filters and columns.
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
  - `create_job`
    - Schedules one-off (historical) jobs for the subscription using ISO date strings and stage IDs that you can source from `get_product_stage_ids`.
    - Example LLM request: `Create one-off jobs for subscription 987 from 2024-01-01 to 2024-01-07 using stage ids [12, 34]`

- Subscriptions
  - `get_subscriptions`
    - Lists all subscriptions for the current user with pagination support. Returns subscription details including product IDs, status, and metadata.
    - Example LLM request: `Show me all my subscriptions`
  - `get_storage_subscriptions`
    - Lists active storage subscriptions linked to the current account. Returns storage-specific subscription details.
    - Example LLM request: `List my storage subscriptions`

- Products & Table Discovery
  - **Interactive workflow**: Use `search_products` → `list_product_tables` → `get_table_schema` for guided table discovery
  - `search_products`
    - Search for Openbridge products by name (case-insensitive). Returns matching products with IDs for use with `list_product_tables`.
    - Example LLM request: `Find products matching "Amazon Ads Sponsored"`
  - `list_product_tables`
    - List tables (payloads) available for a product. Optionally filter by `subscription_id` to show only tables enabled for that subscription based on stage_ids.
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
  - The `validate_query` and `execute_query` tools use AI-powered sampling via the OpenAI API to intelligently analyze SQL queries for safety issues, best practices violations, and potential security concerns.
  - These tools are only available when `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` is configured in your environment.
  - The AI validation checks for: read-only operations, proper LIMIT clauses, suspicious patterns, and SQL injection risks.
  - Get your API key from the [OpenAI Platform](https://platform.openai.com/docs/api-reference/introduction).
  - Cost consideration: Query validation typically uses the `gpt-4o-mini` model (configurable via `FASTMCP_SAMPLING_MODEL`), which is cost-effective for this use case.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Server binds to all interfaces (`0.0.0.0`). Ensure firewall/network rules allow your MCP client to reach `MCP_PORT`.
- Per-client authentication
  - The MCP server uses a single `OPENBRIDGE_REFRESH_TOKEN` for all tool calls; downstream access is shared across clients.
  - Deployers must layer their own client authentication (e.g., network isolation, mTLS proxies, signed client configs, or OS-level ACLs) to ensure only trusted agents can invoke the server.
  - Rotate tokens regularly and monitor access logs to detect misuse when multiple operators share the same deployment.
  - You can also plug FastMCP's standard authentication providers directly into this server (JWT validation, OAuth proxy, WorkOS AuthKit, etc.) if you prefer first-class per-client auth at the MCP layer; choose the provider that aligns with your org's identity stack.
