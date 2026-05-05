import asyncio
import functools
import inspect
import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

from fastmcp import FastMCP
from fastmcp.server.tasks import TaskConfig
from starlette.responses import JSONResponse

from src.server.tools import remote_identity as remote_identity_tools  # noqa: E402
from src.server.tools import service as service_tools  # noqa: E402
from src.server.tools import healthchecks as healthchecks_tools  # noqa: E402
from src.server.tools import jobs as jobs_tools  # noqa: E402
from src.server.tools import products as products_tools  # noqa: E402
from src.server.tools import subscriptions as subscriptions_tools  # noqa: E402
from src.server.tools import capabilities as capabilities_tools  # noqa: E402
from src.server.tools.tool_manifest import TOOL_MANIFEST  # noqa: E402
from src.utils.logging import get_logger  # noqa: E402
from src.auth.authentication import create_auth_middleware, create_openbridge_config  # noqa: E402
from src.auth.manager import get_auth_manager  # noqa: E402
from src.auth.oauth_proxy import OAuthBridgeMiddleware, create_oauth_proxy  # noqa: E402
from src.server.code_mode import create_code_mode_transform, is_code_mode_enabled  # noqa: E402
from src.server.error_envelope_middleware import ErrorEnvelopeMiddleware  # noqa: E402
from src.server.sampling import create_sampling_handler  # noqa: E402


logger = get_logger("mcp_server")


def _get_service_version() -> str:
    """Return installed package version for health endpoint reporting."""
    try:
        return version("openbridge-mcp")
    except PackageNotFoundError:
        return "unknown"


def _log_capability_summary(registered_tool_names: set[str]) -> None:
    capabilities = capabilities_tools.build_capabilities(registered_tool_names)
    summary = capabilities["summary"]
    runtime = capabilities["runtime"]
    logger.info(
        "Capability summary: enabled=%s disabled=%s sampling_key_present=%s llm_validation_enabled=%s code_mode_enabled=%s",
        summary["enabled_tools"],
        summary["disabled_tools"],
        runtime["sampling_key_present"],
        runtime["llm_validation_enabled"],
        runtime["code_mode_enabled"],
    )


def _warn_if_server_token_fallback_open() -> None:
    """Emit a startup WARNING when the server-token fallback is reachable.

    Multi-tenant deployments must set ``OPENBRIDGE_REQUIRE_CLIENT_AUTH=true``
    so requests without a Bearer header are rejected instead of silently
    executing as the principal of ``OPENBRIDGE_REFRESH_TOKEN``. Today the
    flag defaults to false (backward-compat for single-tenant installs)
    — but if an operator has *also* configured a server refresh token,
    that combination is the cross-tenant leak shape the security review
    flagged. Surface it loudly at boot rather than waiting for the wrong
    account to receive someone else's data.
    """
    server_token_set = bool(os.getenv("OPENBRIDGE_REFRESH_TOKEN"))
    if not server_token_set:
        return
    raw_flag = (os.getenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH") or "").strip().lower()
    require_client_auth = raw_flag in {"true", "1", "yes", "on"}
    if require_client_auth:
        return
    logger.warning(
        "OPENBRIDGE_REFRESH_TOKEN is set but OPENBRIDGE_REQUIRE_CLIENT_AUTH is not enabled. "
        "Requests without an Authorization: Bearer header will execute as the server principal. "
        "This is fine for single-tenant deployments; for ANY multi-tenant deployment set "
        "OPENBRIDGE_REQUIRE_CLIENT_AUTH=true to fail closed and avoid cross-tenant data leakage."
    )

def _is_async_callable(func: Callable[..., Any]) -> bool:
    """True for ``async def`` functions and partials wrapping them."""
    target = func
    while isinstance(target, functools.partial):
        target = target.func
    return inspect.iscoroutinefunction(target)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _async_wrap(sync_func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a synchronous tool implementation in an async coroutine.

    FastMCP's ``TaskConfig`` requires task-enabled tools to be async — a
    sync function would raise ``ValueError`` on registration. The
    Openbridge tool layer is currently built on the synchronous
    ``requests`` library; rather than rewrite every tool in this PR we
    offload each sync call to a worker thread via ``asyncio.to_thread``.
    The signature (including ``ctx`` keyword) is preserved via
    ``functools.wraps`` so FastMCP's parameter introspection still
    resolves the right schema.
    """

    @functools.wraps(sync_func)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(sync_func, *args, **kwargs)

    return _wrapper


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server."""
    auth_cfg = create_openbridge_config()
    sampling_handler = create_sampling_handler()

    _MCP_KWARGS = dict(
        name="Openbridge MCP",
        instructions="Openbridge MCP server for utilizing a variety of API endpoints and tools.",
        sampling_handler=sampling_handler,
        tasks=True,
    )

    if auth_cfg.auth_mode == "oauth_proxy":
        # FastMCP's OAuthProxy handles the full OAuth flow and token
        # introspection. The bridge middleware then copies the verified
        # access token into the ContextVar so all existing tools work
        # without modification.
        logger.info("Configuring MCP in oauth_proxy mode with FastMCP OAuthProxy and introspection")
        mcp_port = int(os.getenv("MCP_PORT", 8000))
        mcp_host = os.getenv("MCP_HOST", "localhost")
        base_url = os.getenv("MCP_BASE_URL", f"http://{mcp_host}:{mcp_port}")
        logger.info("OAuth Proxy base URL set to: %s", base_url)
        oauth = create_oauth_proxy(base_url=base_url)
        logger.info("OAuth Proxy created successfully, initializing FastMCP with OAuthProxy auth")
        mcp = FastMCP(**_MCP_KWARGS, auth=oauth)
        mcp.add_middleware(ErrorEnvelopeMiddleware())
        mcp.add_middleware(OAuthBridgeMiddleware())
        logger.info("Auth mode: oauth_proxy (FastMCP OAuthProxy + introspection, base_url=%s)", base_url)
    else:
        # Default: refresh_token mode — existing OpenbridgeAuthMiddleware
        # exchanges Bearer refresh tokens for JWTs.
        auth_manager = get_auth_manager()
        middleware = create_auth_middleware(auth_cfg, jwt_middleware=False, auth_manager=auth_manager)
        mcp = FastMCP(**_MCP_KWARGS)
        mcp.add_middleware(ErrorEnvelopeMiddleware())
        for mw in middleware:
            mcp.add_middleware(mw)
        logger.info("Auth mode: refresh_token (Bearer/exchange middleware)")

    registered_tool_names = set()

    # Default policy: every Openbridge-API tool can be slow (network
    # I/O, paginated calls, history sweeps) so we make background-task
    # execution OPTIONAL — clients choose per call. Tools without
    # task=... fall through to "forbidden" by FastMCP's default.
    DEFAULT_TASK_CONFIG = TaskConfig(mode="optional")

    def register_tool(name: str, func, *, task: TaskConfig | None = DEFAULT_TASK_CONFIG):
        impl = func if _is_async_callable(func) else _async_wrap(func)
        decorator_kwargs: dict[str, Any] = {
            "name": name,
            "description": TOOL_MANIFEST[name]["description"],
        }
        if task is not None:
            decorator_kwargs["task"] = task
        mcp.tool(**decorator_kwargs)(impl)
        registered_tool_names.add(name)

    # Register tools.
    # get_capabilities is a pure local function (no network); leaving it
    # task=None keeps it synchronous and out of the Docket queue.
    register_tool(
        "get_capabilities",
        lambda: capabilities_tools.build_capabilities(registered_tool_names),
        task=None,
    )
    # Remote identity tools
    register_tool("get_remote_identities", remote_identity_tools.get_remote_identities)
    register_tool("get_remote_identity_by_id", remote_identity_tools.get_remote_identity_by_id)
    # Service Tools
    # Query validation tools require an API key for LLM sampling
    has_sampling_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    query_execution_enabled = _env_flag("OPENBRIDGE_ENABLE_QUERY_EXECUTION", default=True)
    if has_sampling_key:
        register_tool("validate_query", service_tools.validate_query)
        if query_execution_enabled:
            register_tool("execute_query", service_tools.execute_query)
        else:
            logger.info("Skipping execute_query: OPENBRIDGE_ENABLE_QUERY_EXECUTION is false")
    else:
        logger.info("Skipping SQL query tools: no API key configured (set FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY)")
    register_tool("get_amazon_api_access_token", service_tools.get_amazon_api_access_token)
    register_tool("get_amazon_advertising_profiles", service_tools.get_amazon_advertising_profiles)
    register_tool("get_table_schema", service_tools.get_table_schema)
    register_tool("get_suggested_table_names", service_tools.get_suggested_table_names)
    # Healthchecks Tools
    register_tool("get_healthchecks", healthchecks_tools.get_healthchecks)
    # Jobs tools
    register_tool("get_jobs", jobs_tools.get_jobs)
    register_tool("get_job_by_id", jobs_tools.get_job_by_id)
    register_tool("get_history_by_id", jobs_tools.get_history_by_id)
    register_tool("update_history_status", jobs_tools.update_history_status)
    register_tool("create_job", jobs_tools.create_job)
    # Subscriptions tools
    register_tool("get_subscriptions", subscriptions_tools.get_subscriptions)
    register_tool("get_subscription_by_id", subscriptions_tools.get_subscription_by_id)
    register_tool("create_subscription", subscriptions_tools.create_subscription)
    register_tool("update_subscription", subscriptions_tools.update_subscription)
    register_tool("cancel_subscription", subscriptions_tools.cancel_subscription)
    register_tool("get_storage_subscriptions", subscriptions_tools.get_storage_subscriptions)
    # Products tools
    register_tool("get_product_stage_ids", products_tools.get_product_stage_ids)
    register_tool("search_products", products_tools.search_products)
    register_tool("list_product_tables", products_tools.list_product_tables)
    register_tool("get_product_card", products_tools.get_product_card)
    register_tool("list_all_product_basic_metadata", products_tools.list_all_product_basic_metadata)

    # Health check endpoint for monitoring and load balancers
    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request):
        """Health check endpoint for monitoring, load balancers, and deployment platforms."""
        return JSONResponse({
            "status": "healthy",
            "service": "openbridge-mcp",
            "version": _get_service_version(),
        })

    if is_code_mode_enabled():
        try:
            transform = create_code_mode_transform()
            mcp.add_transform(transform)
            logger.info(
                "Code mode active: tool surface is meta-tools (search/get_schema/execute). "
                "This is the recommended client entry point. Set CODE_MODE=false to expose the direct tool catalog instead."
            )
        except ImportError as exc:
            logger.error(
                "Code mode requested but dependencies are missing (%s). "
                "Falling back to direct tool catalog. Install requirements with fastmcp[code-mode].",
                exc,
            )
    else:
        logger.warning(
            "Code mode disabled (CODE_MODE=false). Exposing direct tool catalog as a fallback. "
            "Code mode is the recommended primary entry point — re-enable it (unset CODE_MODE or set CODE_MODE=true) unless you have a specific reason to expose every tool by name."
        )

    _log_capability_summary(registered_tool_names)
    if auth_cfg.auth_mode != "oauth_proxy":
        _warn_if_server_token_fallback_open()
    return mcp
