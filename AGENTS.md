# Openbridge MCP Development Guidelines

> **Audience**: LLM-driven engineering agents and human developers

The Openbridge MCP Server is a FastMCP-based server enabling LLMs to interact with the Openbridge platform APIs. This guide covers development workflows, testing, and contribution standards for Python ≥3.13.

## Required Development Workflow

**CRITICAL**: Always run these commands in sequence before committing:

```bash
make lint        # Ruff linter on src/ and tests/
make check       # Static syntax check (compileall)
make test        # AUTH_ENABLED=false pytest tests/ -v
```

**All three must pass** - this is enforced by CI in `.github/workflows/ci.yml`

Alternative quick check: `make all` runs lint + check + test in sequence.

**Tests must pass and linting must be clean before committing.**

## Repository Structure

| Path | Purpose |
|------|---------|
| `main.py` | Entry point: loads `.env`, primes `src/`, starts FastMCP server |
| `src/server/` | MCP server runtime wiring |
| `├─mcp_server.py` | Tool registration and server configuration |
| `├─tools/` | Tool implementations (jobs, healthchecks, subscriptions, etc.) |
| `├─sampling.py` | LLM sampling handler for query validation |
| `src/auth/` | JWT token exchange, authentication middleware |
| `├─simple.py` | Openbridge token refresh logic |
| `├─authentication.py` | FastMCP middleware for JWT injection |
| `src/utils/` | Shared utilities (logging, security validation) |
| `src/models/` | Pydantic schemas for API requests/responses |
| `tests/` | pytest suite mirroring `src/` layout |
| `├─server/tools/` | Tool-specific tests with mocked HTTP |
| `Makefile` | Development commands (setup, test, lint, serve) |
| `.github/workflows/` | CI/CD automation (Python 3.13) |

## Adding New Tools

When adding MCP tools, changes typically span:

1. **Implement** the tool function under `src/server/tools/`
2. **Register** it in `src/server/mcp_server.py` using `@mcp.tool` decorator
3. **Add tests** under `tests/server/tools/` mirroring the module path
4. **Document** any new environment variables in:
   - This AGENTS.md file
   - README.md (user-facing .env template)
   - `fastagent.config.yaml` (for MCP client configs)

Example registration pattern:
```python
# src/server/mcp_server.py
mcp.tool(
    name='tool_name',
    description='Clear, concise description of what this tool does.',
)(module.function_name)
```

## Skills

The repo ships a FastMCP skill at `skills/openbridge-mcp/` that's auto-loaded into the running server via `SkillsDirectoryProvider` (registered in `src/server/mcp_server.py`). Connected MCP clients see it as **resources**, not tools or prompts:

- `skill://openbridge-mcp/SKILL.md` — main instruction file
- `skill://openbridge-mcp/_manifest` — synthetic JSON listing every file in the skill bundle (path, size, hash)
- `skill://openbridge-mcp/references/<name>.md` — supporting reference docs (workflows, code-mode, error-envelope, embed-cli, tools-catalog)
- `skill://openbridge-mcp/evals/evals.json` — eval fixtures
- `skill://openbridge-mcp/mcp-servers.json` — bundled MCP server list

Discovery from a client:
```python
async with Client("https://mcp.openbridge.com/mcp", auth=...) as client:
    resources = await client.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith("skill://")]
    skill_md = await client.read_resource("skill://openbridge-mcp/SKILL.md")
```

### Adding a new skill

1. Create a new directory under `skills/` (e.g., `skills/my-skill/`).
2. Add `SKILL.md` with YAML frontmatter at the top:
   ```markdown
   ---
   name: my-skill
   description: One paragraph that triggers the skill.
   version: "0.1.0"
   ---
   # ...skill content...
   ```
3. Optional supporting files in `references/` or `evals/` subdirs are auto-exposed because the provider is wired with `supporting_files="resources"`.
4. Rebuild the Docker image (`docker compose up --build`) so `COPY skills/ /app/skills/` picks up the new directory. Reload is **off** in production — skills snapshot at image build time.
5. The provider config lives at `src/server/mcp_server.py:_resolve_skills_root()` and the registration block. No change needed when adding new skill subdirectories.

### Caveats

- Skill content lives behind the same auth boundary as tools (the FastMCP middleware chain wraps resource calls too). `OPENBRIDGE_REQUIRE_CLIENT_AUTH=true` deployments require a Bearer token to read skill resources.
- If `skills/` is absent (stripped install), the server boots cleanly with zero skill resources — `_resolve_skills_root()` returns `None` and registration is skipped.

### Tool-only host workaround (`list_skills` / `read_skill`)

Some MCP hosts only route `tools/*` traffic into the assistant context. Claude.ai's MCP host as of 2026-05-01 is the verified example — its Code Mode sandbox can call `call_tool(...)` but has no analogue for `list_resources()` / `read_resource()`. To make skills discoverable from inside such hosts, two meta-tools wrap the resource API:

- **`list_skills()`** → returns `[{uri, name, description, mime_type}, ...]` for every `skill://` resource.
- **`read_skill(uri)`** → returns `{uri, content, mime_type}` for the body of a single skill resource.

Both call `ctx.fastmcp.list_resources()` / `ctx.fastmcp.read_resource()` in-process — the call never crosses the wire, so the host's tool-channel filter doesn't apply. Errors return v1 envelopes.

Hosts that natively surface MCP resources (Claude Code, MCP Inspector, FastMCP `Client` SDK) should keep using the resource API directly — it's the FastMCP-blessed pattern. The wrappers exist solely as a host-compatibility shim; they're registered as `meta` category in `TOOL_MANIFEST` and visible from `get_capabilities`.

## Table Discovery Workflow

The MCP server provides an interactive, multi-step workflow for discovering tables and schemas:

### 3-Step Discovery Process

1. **Search for products** → User selects a product
2. **List tables** for that product → User selects a table
3. **Get schema** for that table → Returns schema details

### Available Tools

**`search_products(query: str)`**
- Search for Openbridge products by name (case-insensitive substring matching)
- Example: `search_products("Amazon Ads Sponsored")`
- Returns: `[{"id": 50, "name": "Amazon Ads - Sponsored Brands", "worker_name": "amzadsponsoredbrands"}, ...]`

**`list_product_tables(product_id: int, subscription_id: Optional[int] = None)`**
- List tables (payloads) available for a product
- If `subscription_id` provided, filters tables by subscription's stage_ids (from `/spm` API)
- Example: `list_product_tables(product_id=50)`
- Returns: `[{"name": "amzn_ads_sb_campaigns", "stage_id": 1004, "id": 2184}, ...]`

**`get_table_schema(table_name: str)`**
- Get detailed schema/rules for a table from the Rules API
- Use table names from `list_product_tables` output
- Example: `get_table_schema("amzn_ads_sb_campaigns")`
- Returns: Full schema with fields, types, rules, etc.

### Example Conversation Flow

**User:** "Show me tables for Amazon Advertising Sponsored Ads"

**LLM:** Calls `search_products("Amazon Advertising Sponsored Ads")`
**Returns:** 3 matching products

**LLM to User:** "Found these products:
1. Amazon Ads - Sponsored Products (ID: 48)
2. Amazon Ads - Sponsored Brands (ID: 50)
3. Amazon Ads - Sponsored Display (ID: 49)
Which one?"

**User:** "Sponsored Brands"

**LLM:** Calls `list_product_tables(product_id=50)`
**Returns:** List of tables

**LLM to User:** "Tables for Amazon Ads - Sponsored Brands:
- amzn_ads_sb_campaigns
- amzn_ads_sb_adgroups
- amzn_ads_sb_keywords
Which schema do you want?"

**User:** "campaigns"

**LLM:** Calls `get_table_schema("amzn_ads_sb_campaigns")`
**Returns:** Complete schema

### Subscription-Based Filtering

For subscription-specific table lists:

```python
# Get tables enabled for a specific subscription
list_product_tables(product_id=50, subscription_id=128853)
```

This follows the workflow documented in `product-tables.md`:
1. Fetches subscription → `product_id` + `stage_ids` from `/spm` API
2. Falls back to `/sub/{id}` for legacy subscriptions (stage_id=0)
3. Filters payloads to only those matching subscription's stage_ids

## Environment Variables

Build a local `.env` from the template in README.md. **Never commit real secrets.**

### Required Variables

- **Server**
  - `MCP_PORT` (default `8000`): Port for HTTP MCP server
  - `MCP_HOST` (optional, default `0.0.0.0`): Host/interface to bind the HTTP MCP server
  - `MCP_STATELESS_HTTP` (optional, default `true`): Run FastMCP HTTP transport in stateless mode (fresh transport per request)
    - Default `true` is safe for multi-instance deployments behind an L7 LB without sticky sessions
    - Set `false` only if you need streamable HTTP session reuse and have session affinity guaranteed

- **Background tasks (SEP-1686 / Docket)**
  - `FASTMCP_DOCKET_URL` (default in compose: `redis://redis:6379/0`; out-of-compose default: `memory://`):
    - Production: the Redis sidecar declared in `docker-compose.yml`. Internal-only (no published ports), reachable from openbridge-mcp via the compose network.
    - `memory://` is single-process and ephemeral — fine for `make test` and local `python main.py` runs without compose.
    - Tests pin this to `memory://` via fixtures so the suite stays offline.
  - `FASTMCP_DOCKET_CONCURRENCY` (optional, default `10`): max concurrent background tasks per worker.
  - Every Openbridge-API tool registers with `task=TaskConfig(mode="optional")`; clients choose sync vs background per call. `get_capabilities` is exempt (no I/O).

- **Code mode (primary client entry point)**
  - `CODE_MODE` (default `true`, **recommended on**): Code Mode is the primary surface — clients see only `tags`/`search`/`get_schema`/`execute`. `CODE_MODE=false` exposes the full direct catalog and emits a startup WARNING.
  - `CODE_MODE_INCLUDE_TAGS` (default `true`): include the optional `tags` discovery meta-tool.
  - `CODE_MODE_MAX_DURATION_SECS` (default `30`), `CODE_MODE_MAX_MEMORY` (default `50000000`): sandbox limits.

- **Authentication (Dual-Mode)**
  - `OPENBRIDGE_REFRESH_TOKEN` (optional): Token in format `xxx:yyy` for server-side authentication
    - When set: Server exchanges refresh token for JWTs automatically
    - When unset: Clients must provide `Authorization: Bearer <token>` headers
    - Client tokens take precedence over server tokens
    - Server starts successfully without this variable, enabling pure client-side auth
  - `OPENBRIDGE_REQUIRE_CLIENT_AUTH` (optional, default `false`): **Multi-tenant safety gate**
    - When `true`: requests without an `Authorization: Bearer` header are rejected with `McpError(-32001)` instead of falling back to `OPENBRIDGE_REFRESH_TOKEN`
    - **Required for any deployment serving more than one Openbridge account** — without it, an unauthenticated request silently executes as the server principal (cross-tenant leak)
    - Leave `false` for single-tenant or local-dev installs where the server token fallback is intentional
    - Backstopped at the tool layer: `src/server/tools/base.py` raises `AuthenticationError` if a tool is invoked without a primed JWT while this flag is on
  - `OPENBRIDGE_API_TIMEOUT` (optional, default `30`): Read timeout (seconds) for Openbridge HTTP requests
    - Connect timeout is fixed at 10 seconds
  - `OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES` (optional, default `256`): Per-process LRU cap on cached client refresh-token → JWT mappings
    - Eviction is LRU (active tenants stay resident under churn); replaces the legacy FIFO-32 behavior
    - Tune up for high-tenant-count deployments; tune down for memory-constrained environments
    - For shared cross-replica caching, swap `_InMemoryLRUTokenCache` for a `TokenCache`-conforming Redis adapter (see `src/auth/simple.py`)

- **Authentication — URL-Embedded Auth / Path Tokens** (`refresh_token` mode only)
  - `MCP_PATH_TOKEN_SECRET` (required for stable production tokens): Shared HS256 signing secret for JWTs embedded in connection URLs. Must match the secret used by the Openbridge console (or any external service generating path tokens). If unset, an ephemeral secret is generated on startup — all path tokens are invalidated on restart. Generate with `openssl rand -hex 32`.
  - `MCP_PATH_TOKEN_TTL_DAYS` (optional, default `30`): Lifetime in days for tokens generated by `build_connection_url` or `PathTokenMiddleware.generate_token`. External tokens are accepted until their own `exp` claim expires regardless of this setting.
  - **Implementation:** `src/auth/path_token_middleware.py` — `PathTokenMiddleware` (pure ASGI), `build_connection_url`, `_sign_path_token`, `_verify_path_token`, `load_secret`
  - **Verification:** signature (HS256), expiry (`exp`), audience (`aud == "openbridge-mcp"`). Issuer (`iss`) is not validated — any signing service that shares the secret is accepted.
  - **ASGI placement:** `PathTokenMiddleware` wraps the FastMCP `StarletteWithLifespan` app at the outermost uvicorn level in `main.py` (`server.http_app()` → wrap → `uvicorn.run()`). Must be outermost because `/mcp/{token}` has no registered Starlette route — placing it inside the app via `server.run(middleware=[...])` causes 404.

- **Authentication (OAuth Proxy Mode)** — activated by `OPENBRIDGE_AUTH_MODE=oauth_proxy`
  - `OPENBRIDGE_AUTH_MODE` (optional, default `"refresh_token"`): Auth mode selector.
    - `"refresh_token"`: Default — `OpenbridgeAuthMiddleware` exchanges Bearer refresh tokens for JWTs.
    - `"oauth_proxy"`: FastMCP's built-in `OAuthProxy` handles the full OAuth 2.0 authorization code flow, proxying to Openbridge's OAuth endpoints and verifying tokens via introspection. Mutually exclusive with `refresh_token` mode — cannot coexist with `OpenbridgeAuthMiddleware`.
  - `MCP_BASE_URL` (required for `oauth_proxy` in production): Externally-reachable base URL of the MCP server, used by FastMCP to construct the OAuth redirect URI. Example: `https://mcp.example.com`. Defaults to `http://{MCP_HOST}:{MCP_PORT}` — always set this explicitly when running behind a reverse proxy.
  - `MCP_JWT_SIGNING_KEY` (recommended for `oauth_proxy`): Stable secret used by FastMCP to sign the session tokens it issues to MCP clients. If unset, a random UUID is generated per process start — MCP sessions will not survive server restarts. Set to a strong, stable secret for production deployments.
  - `OPENBRIDGE_OAUTH_CLIENT_ID` (optional, default `"openbridge-mcp"`): Client ID sent to the Openbridge introspection endpoint. The endpoint reads credentials from embedded secrets; this value is forwarded but typically not validated.
  - `OPENBRIDGE_OAUTH_CLIENT_SECRET` (optional, default `"not-used"`): Client secret for the introspection endpoint. Same semantics as `OPENBRIDGE_OAUTH_CLIENT_ID`.
  - `OPENBRIDGE_OAUTH_UPSTREAM_CLIENT_ID` (optional, default `""`): Upstream `client_id` forwarded to `/auth/oauth/initialize`. Openbridge reads this from embedded secrets; leave empty unless instructed otherwise.

- **Query Validation (AI-powered)**
  - `FASTMCP_SAMPLING_API_KEY` or `OPENAI_API_KEY` (optional): Required to enable `validate_query` and `execute_query` tools
    - Without this, query validation tools are not registered
  - `OPENBRIDGE_ENABLE_QUERY_EXECUTION` (optional, default `true`): Controls `execute_query` registration independently
    - When `false`, `validate_query` remains available but `execute_query` is not registered
  - `FASTMCP_SAMPLING_MODEL` (optional, default `gpt-4o-mini`): OpenAI model for query validation
  - `FASTMCP_SAMPLING_BASE_URL` (optional): Custom OpenAI-compatible API endpoint
  - `OPENBRIDGE_ENABLE_LLM_VALIDATION` (optional, default `false`): Opt-in to send SQL to LLM
    - When disabled, uses heuristics only (no SQL leaves your environment)

- **Code Mode (default enabled)**
  - `CODE_MODE` (optional, default `true`): Enables FastMCP code mode as the standard MCP surface (`search`/`get_schema`/`execute`)
  - `CODE_MODE_INCLUDE_TAGS` (optional, default `true`): Adds `tags` discovery meta-tool
  - `CODE_MODE_MAX_DURATION_SECS` (optional, default `30`): Sandbox execution timeout for `execute`
  - `CODE_MODE_MAX_MEMORY` (optional, default `50000000`): Sandbox memory limit in bytes for `execute`

- **Logging**
  - `LOG_LEVEL` (optional, default `INFO`): Application log level
  - `LOG_FORMAT` (optional, default `structured`): Log output format (`structured` JSON or `simple` text)

- **Service API Base URLs** (see README.md for full list)
  - `SERVICE_API_BASE_URL`
  - `REMOTE_IDENTITY_API_BASE_URL`
  - `HISTORY_API_BASE_URL`
  - etc.

**Document new variables**: When adding env vars, update AGENTS.md, README.md, and fastagent.config.yaml.

## Development Commands

The project uses a Makefile for consistency with CI:

```bash
make setup      # Create venv with uv (Python 3.13)
make install    # Install deps from pyproject.toml (project + [dev] extras)
make lint       # Run ruff check src/ tests/
make lint-fix   # Auto-fix linting issues
make format     # Format code with ruff
make check      # Static syntax check (python -m compileall)
make test       # Run pytest with AUTH_ENABLED=false
make serve      # Start MCP server (python main.py)
make clean      # Remove cache files and artifacts
make all        # Run lint + check + test
```

**Standard workflow:**
```bash
# Initial setup
make setup
source .venv/bin/activate
make install

# Development cycle
make lint-fix   # Fix auto-fixable issues
make test       # Verify tests pass
make serve      # Test locally

# Before committing
make all        # Ensure everything passes
```

Ensure `MCP_PORT` in `.env` matches your MCP client config and docker-compose mapping.

## Testing Best Practices

### Testing Standards

- Every test: atomic, self-contained, tests single functionality
- Use `@pytest.mark.parametrize` for multiple examples of same functionality
- Use separate tests for different functionality pieces
- **ALWAYS** put imports at the top of the file, not in test body
- **ALWAYS** run `make test` after significant changes
- Use `responses` or `pytest-httpx` to stub Openbridge HTTP traffic
- Cover success paths AND error branches (missing env vars, API failures, etc.)

### Test Organization

Mirror `src/` structure in `tests/`:
```
src/server/tools/jobs.py     → tests/server/tools/test_jobs.py
src/auth/authentication.py   → tests/auth/test_authentication.py
```

### Example Test Pattern

```python
import pytest
import responses
from src.server.tools import jobs

@responses.activate
def test_create_job_success():
    """Test job creation with valid inputs."""
    responses.post(
        "https://history.api.test/history/123",
        json={"data": {"attributes": {"job_id": "456"}}},
        status=200
    )

    result = jobs.create_job(
        subscription_id=123,
        date_start="2024-01-01",
        date_end="2024-01-31",
        stage_ids=[1]
    )

    assert result[0]["job_id"] == "456"
```

## Coding Style & Naming Conventions

### Python Style

- Follow PEP 8 with 4-space indentation
- Type hints on all function signatures
- Concise docstrings as in `src/models/base_models.py`
- Python ≥3.13 required (CI tests Python 3.13)

### Naming Conventions

- Modules, functions, files: `snake_case`
- Classes: `PascalCase` matching API nouns (`OpenbridgeAuth`, `IdentityListResponse`)
- Tools: Clear, action-oriented names (`get_jobs`, `create_job`, not `jobs_getter`)
- Tool names: singular for single items, plural for lists

### Data Structures

- Use Pydantic models for request/response shapes
- Avoid ad-hoc dicts inside tools to preserve validation
- Keep stylistic changes separate from behavior updates
- Numeric IDs in tool signatures are strict integers. Use `123`, not `"123"`.

## Git & Commit Standards

### Commit Messages and Agent Attribution

- **Agents MUST identify themselves** in commits with:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  Co-Authored-By: Claude <noreply@anthropic.com>
  ```
- Keep commit messages concise - focus on **what** changed
- Use imperative mood: "Add healthchecks pagination" not "Added pagination"
- Group related work in single commits
- Reference issues in commit body when applicable

### Pull Request Guidelines

**Required PR Structure:**
- **1-2 paragraphs**: Describe the problem/tension and the solution
- **Focused code example**: Show key capability with before/after if applicable
- **Test evidence**: Note that tests pass (`make all` clean)

**Avoid:**
- Bullet-point summaries of every file changed
- Exhaustive change lists
- Verbose "Closes #123" or "Fixes #456" - just reference the issue naturally
- Marketing language or overselling changes

**Do:**
- Be opinionated about why the change matters
- Show impact with concrete examples
- Keep minor fixes short and direct
- Respond to review comments with follow-up commits (no force pushes)

### CI/CD

- CI runs on Python 3.13
- All checks must pass: lint (ruff), static check (compileall), tests (pytest)
- Never amend commits just to fix linting - make a new commit

## Writing Style

- Be brief and to the point
- **NEVER** use "This isn't..." or "not just..." constructions
  - ❌ "This isn't just a wrapper, it's a full client"
  - ✅ "This is a full client for the Openbridge API"
- State what something **IS** directly - avoid defensive patterns
- Explain **why** changes matter, not just what changed

## Documentation

**Core Principle:** A feature doesn't exist unless it is documented!

When adding features:
1. Update AGENTS.md if workflow changes
2. Update README.md for user-facing features
3. Update fastagent.config.yaml for new env vars
4. Add docstrings to all public functions
5. Include usage examples in tool descriptions

Documentation should:
- Explain before showing code
- Make code examples fully runnable (include imports)
- Use clear headers for navigation
- Motivate features (why) before mechanics (how)

## Code Review Guidelines

### Philosophy

Code review maintains codebase health while helping contributors succeed. A well-written PR that adds unwanted functionality must still be rejected. Code must advance the codebase in the intended direction.

### Focus On

- **Alignment**: Does this advance the codebase as intended?
- **API design**: Are names clear? Is the interface ergonomic?
- **Maintainability**: Would you be comfortable maintaining this forever?
- **Suggest specific improvements**: Not generic "add more tests" comments
- **Think like a user**: Consider learning curve and developer experience

### For Agent Reviewers

- **Read full context**: Examine related files, tests, docs before reviewing
- **Check established patterns**: Look for consistency with existing code
- **Verify functionality**: Understand what it actually does, not just claims
- **Consider edge cases**: Think through error conditions and boundaries

### Avoid

- Generic feedback without specifics
- Hypothetical problems unlikely to occur
- Bikeshedding style preferences when functionality is correct
- Requesting changes without suggesting solutions
- Summarizing what the PR already describes

### Decision Framework

Before approving, ask:
1. Does this PR achieve its stated purpose?
2. Is that purpose aligned with project goals?
3. Would I be comfortable maintaining this code?
4. Have I actually understood what it does?
5. Does this introduce technical debt?

## FastMCP Compatibility

- The repo currently uses `fastmcp>=3.1.0` (see `pyproject.toml`)
- Context state API: Native `ctx.set_state()` and `ctx.get_state()` available
- Compatibility shim in `src/auth/authentication.py:19-31` handles older FastMCP releases gracefully
- If you upgrade FastMCP, update this section and verify middleware, context state, and tool contracts still work

## Security Considerations

- Never commit secrets to version control (`.env` is in `.gitignore`)
- Validate all external inputs (see `src/utils/security.py`)
- Use `safe_pagination_url()` for untrusted pagination links (prevents SSRF)
- Client JWTs are passed through by middleware; token signature and authorization checks are enforced by downstream Openbridge APIs
- Query validation tools prevent SQL injection via LLM sampling + heuristics
- Set `OPENBRIDGE_ENABLE_LLM_VALIDATION=false` if SQL must not leave your environment

## Common Workflows

### Adding a New Tool

1. **Create implementation**: `src/server/tools/my_feature.py`
   ```python
   from typing import Optional
   from fastmcp.server.context import Context

   def my_tool(param: str, ctx: Optional[Context] = None) -> dict:
       """Clear description of what this tool does."""
       # Implementation
       return {"result": "value"}
   ```

2. **Register in server**: `src/server/mcp_server.py`
   ```python
   from src.server.tools import my_feature

   mcp.tool(
       name='my_tool',
       description='Clear description matching the docstring.',
   )(my_feature.my_tool)
   ```

3. **Add tests**: `tests/server/tools/test_my_feature.py`
   ```python
   import pytest
   from src.server.tools import my_feature

   def test_my_tool_success():
       result = my_feature.my_tool("test")
       assert result["result"] == "value"
   ```

4. **Run validation**:
   ```bash
   make test       # Verify tests pass
   make lint       # Check code style
   ```

### Debugging Issues

1. Check logs: `docker compose logs -f openbridge-mcp`
2. Verify env vars: Ensure `.env` has all required variables
3. Test auth: `OPENBRIDGE_REFRESH_TOKEN` must be in `xxx:yyy` format
4. Run local: `AUTH_ENABLED=false make serve` to bypass auth during dev
5. Check port: Server runs on `MCP_PORT` (default 8000)

## Getting Help

- Read existing tests for patterns: `tests/server/tools/test_*.py`
- Check FastMCP docs: https://gofastmcp.com
- Review recent PRs for examples
- Open an issue for questions or clarifications
