# Repository Guidelines

## Project Structure & Module Organization
- `main.py` loads `.env`, primes `src/`, and starts the FastMCP server.
- `src/server/` houses runtime wiring; register new tool classes in `server/mcp_server.py` and place implementations under `tools/`.
- `src/auth/`, `src/config/`, `src/models/`, `src/utils/` cover token exchange, config helpers, Pydantic schemas, and logging; agent assets sit in `src/server/agents/`.
- Mirror the `src/` layout when adding tests or fixtures (e.g., `tests/server/tools/test_healthchecks.py`) to keep imports straightforward.

## Environment & Configuration
- Build a local `.env` from the template in `README.md`; never commit real secrets.
- Core keys: `MCP_PORT`, `OPENBRIDGE_REFRESH_TOKEN`, and each Openbridge base URL (`SERVICE_API_BASE_URL`, `REMOTE_IDENTITY_API_BASE_URL`, etc.).
- Document any new env var both in this guide and in `fastagent.config.yaml` comments so MCP client configs stay in sync.
- Optional fallback sampling uses `FASTMCP_SAMPLING_API_KEY` (or `OPENAI_API_KEY`), with `FASTMCP_SAMPLING_MODEL`/`FASTMCP_SAMPLING_BASE_URL` tuning the provider; query tools require a `LIMIT` unless callers opt into `allow_unbounded`.
- Deployers run a single shared `OPENBRIDGE_REFRESH_TOKEN`; layer per-client controls (VPN allowlists, mTLS gateways, signed configs) so only trusted operators invoke the MCP.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` prepares an isolated Python 3.10+ workspace.
- `pip install fastmcp python-dotenv requests pydantic` reinstalls the runtime dependencies used across `src/`.
- `MCP_PORT=8010 python main.py` runs the HTTP server on `0.0.0.0`; match the port with your client configuration.
- `pytest` (once the `tests/` tree exists) exercises the suite; target subsets via `pytest tests/server/test_service.py -k execute_query`.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation, type hints, and concise docstrings as in `src/models/base_models.py`.
- Modules, functions, and files use `snake_case`; classes map to API nouns (`OpenbridgeTokenResponse`, `IdentityListResponse`).
- Keep request/response shapes in Pydantic models; avoid ad-hoc dicts inside tools to preserve validation.
- Run your formatter of choice before opening a PR and keep stylistic changes separate from behavior updates.

## Testing Guidelines
- Store tests under `tests/`, mirroring package paths (`tests/server/tools/test_execute_query.py`).
- Stub Openbridge HTTP traffic with `responses` or `pytest-httpx`, asserting both success payloads and error branches.
- Cover new tools, token refresh edges, and missing env var paths; summarize coverage deltas in PR descriptions.

## Commit & Pull Request Guidelines
- Commit subjects stay imperative and concise (e.g., `Add healthchecks pagination`) and group related work.
- Reference issues in commit bodies or PR text, and call out configuration or schema updates explicitly.
- PRs should list purpose, testing evidence (`pytest`, manual MCP run), and screenshots for agent-facing changes; respond to review comments with follow-up commits instead of force pushes.
