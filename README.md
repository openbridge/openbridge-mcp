## Openbridge MCP Server

The *Openbridge MCP Server* is a FastMCP server which enables LLMs to perform various tasks within the Openbridge platform. 

## Deployment
Detailed below are setup and configuration instructions for a local machine, but the same steps can be taken to deploy the MCP on a remote server hosted on [AWS Fargate/EC2](https://aws.amazon.com/fargate), [Google Cloud Plaform (GCP)](https://cloud.google.com/blog/topics/developers-practitioners/build-and-deploy-a-remote-mcp-server-to-google-cloud-run-in-under-10-minutes), or any other remote server technology that best fits your environment.

### Docker deployment
1. Create a `.env` file at the project root with the variables listed below. The compose file mounts it into the container at `/app/.env`.
2. Build and start the stack: `docker compose up --build -d openbridge-mcp`
   - The compose file maps `8010:8010`; update both the port mapping and `MCP_PORT` in `.env` if you need a different port.
3. Check logs with `docker compose logs -f openbridge-mcp` until you see “FastMCP server listening”.
4. Connect your MCP client to `http://localhost:8010/mcp` (or the port you chose).

If you prefer raw Docker commands, run `docker buildx -t openbridge-mcp .` and then start it with `docker run --env-file .env -p 8010:8010 --name openbridge-mcp openbridge-mcp`. Add `--restart unless-stopped` if you want it to survive host restarts.

### Local deployment
As a prerequisite, we recommend using [**uv**](https://docs.astral.sh/uv/) to create and configure a virtual environment.

1. Create a `.env` in the project's root folder (see Variables below). At minimum set `MCP_PORT` and `OPENBRIDGE_REFRESH_TOKEN`.
2. Run the command `uv venv --python 3.12.7 && uv pip install -r requirements.txt`
3. Start the server:
   - Python: `python main.py`
   - The server listens on `0.0.0.0:${MCP_PORT}` using HTTP transport.
4. Connect from an MCP client. Example for the `fastagent` `mcp-remote` client is shown below.

### Environment variables (.env)
Required for server and tools to function. Values typically point to your environment (dev/stage/prod) of Openbridge APIs.

- Server
  - `MCP_PORT` (default `8010`): Port for the HTTP MCP server.
- Authentication
  - `OPENBRIDGE_REFRESH_TOKEN` (optional): Refresh token or bearer token used to obtain/send Authorization headers. If not provided, authentication will be the responsibility of the MCP client.

Example `.env` template:
```bash
# Server settings
MCP_PORT=8010

# Authentication settings
OPENBRIDGE_REFRESH_TOKEN=xxx:yyy
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
        "http://localhost:8010/mcp",
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

- Query
  - `validate_query`
    - Runs a safety check against the FastMCP sampling guardrails to confirm a query is read-only and contains a `LIMIT` unless you explicitly pass `allow_unbounded=True`.
    - Example LLM request: `Validate this SQL against key finance and confirm it has a LIMIT 25`
  - `execute_query`
    - Executes SQL through the Service API after validation succeeds; override the safeguard with `allow_unbounded=True` only when you intend to run without a `LIMIT`.
    - Example LLM request: `Execute the validated SQL on key merchandising with LIMIT 100`

- Rules - see [our data catalog documentation](https://docs.openbridge.com/en/articles/2247373-data-catalog-how-we-organize-and-manage-data-in-your-data-lake-or-cloud-warehouse) for more information.
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

- Healthchecks - see [our documentation about healthchecks](https://docs.openbridge.com/en/articles/6906772-how-to-use-healthchecks) for more information.
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
  - If present, the server will attempt to exchange the `OPENBRIDGE_REFRESH_TOKEN` environment variable (or supplied by your `.env` file) for a JWT.
  - If `OPENBRIDGE_REFRESH_TOKEN` is not set, the MCP client must provide the authentication header as described above.
- Error handling
  - Tools return empty lists or dictionaries with an `error` key when API calls fail; check responses for errors.
- Networking
  - Server binds to all interfaces (`0.0.0.0`). Ensure firewall/network rules allow your MCP client to reach `MCP_PORT`.
- Per-client authentication
  - The MCP server uses a single `OPENBRIDGE_REFRESH_TOKEN` for all tool calls; downstream access is shared across clients.
  - Deployers must layer their own client authentication (e.g., network isolation, mTLS proxies, signed client configs, or OS-level ACLs) to ensure only trusted agents can invoke the server.
  - Rotate tokens regularly and monitor access logs to detect misuse when multiple operators share the same deployment.
  - You can also plug FastMCP's standard authentication providers directly into this server (JWT validation, OAuth proxy, WorkOS AuthKit, etc.) if you prefer first-class per-client auth at the MCP layer; choose the provider that aligns with your org's identity stack.

## Development

### Setup

Use the provided Makefile for common development tasks. All commands mirror the CI workflow to ensure consistency.

1. **Initial setup** (create virtual environment):
   ```bash
   make setup
   source .venv/bin/activate  # or `. .venv/bin/activate`
   ```

2. **Install dependencies** (within active venv):
   ```bash
   make install
   ```

### Running Tests

Run the full test suite:
```bash
make test
```

This sets `AUTH_ENABLED=false` and runs pytest with verbose output, matching the CI environment.

### Code Quality

**Lint your code** before committing:
```bash
make lint
```

**Auto-fix linting issues**:
```bash
make lint-fix
```

**Format code**:
```bash
make format
```

**Static syntax check**:
```bash
make check
```

**Run all quality checks** (lint + syntax + tests):
```bash
make all
```

### Starting the Server Locally

```bash
make serve
```

This runs `python main.py` with your `.env` configuration.

### Makefile Targets

Run `make help` to see all available targets:

```
Available targets:
  setup          Install dependencies in a new virtual environment
  install        Install all dependencies (requires active venv)
  test           Run tests with pytest
  lint           Run linter (ruff) on src and tests
  lint-fix       Run linter and auto-fix issues
  format         Format code with ruff
  check          Run static syntax check
  serve          Start the MCP server
  clean          Remove Python cache files and artifacts
  all            Run all quality checks
```

### CI/CD

The project uses GitHub Actions for continuous integration. On every pull request and push to `main`/`dev`:
- Tests run on Python 3.10.15 and 3.12.7
- Code is linted with ruff
- Static syntax validation runs via `compileall`
- Test results are uploaded as artifacts

See `.github/workflows/ci.yml` for the full configuration.
