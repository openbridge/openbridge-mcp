import os
from importlib.metadata import PackageNotFoundError, version
from fastmcp import FastMCP
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
from src.server.code_mode import create_code_mode_transform, is_code_mode_enabled  # noqa: E402
from src.server.sampling import create_sampling_handler  # noqa: E402


logger = get_logger("mcp_server")


def _get_service_version() -> str:
    """Return installed package version for health endpoint reporting."""
    try:
        return version("openbridge-mcp")
    except PackageNotFoundError:
        return "unknown"


def _log_capability_summary() -> None:
    capabilities = capabilities_tools.build_capabilities()
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

def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server."""
    # Create middleware stack

    # Configure JWT middleware
    auth_cfg = create_openbridge_config()
    auth_manager = get_auth_manager()
    middleware = create_auth_middleware(auth_cfg, jwt_middleware=False, auth_manager=auth_manager)

    sampling_handler = create_sampling_handler()

    # Initialize FastMCP server
    mcp = FastMCP(
        name="Openbridge MCP",
        instructions="Openbridge MCP server for utilizing a variety of API endpoints and tools.",
        sampling_handler=sampling_handler,
    )
    for mw in middleware:
        mcp.add_middleware(mw)

    registered_tool_names = set()

    def register_tool(name: str, func):
        mcp.tool(
            name=name,
            description=TOOL_MANIFEST[name]["description"],
        )(func)
        registered_tool_names.add(name)

    # Register tools
    register_tool(
        "get_capabilities",
        lambda: capabilities_tools.build_capabilities(registered_tool_names),
    )
    # Remote identity tools
    register_tool("get_remote_identities", remote_identity_tools.get_remote_identities)
    register_tool("get_remote_identity_by_id", remote_identity_tools.get_remote_identity_by_id)
    # Service Tools
    # Query validation tools require an API key for LLM sampling
    has_sampling_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if has_sampling_key:
        register_tool("validate_query", service_tools.validate_query)
        register_tool("execute_query", service_tools.execute_query)
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
                "Code mode active: tool surface is meta-tools (search/get_schema/execute). Set CODE_MODE=false to opt out."
            )
        except ImportError as exc:
            logger.error(
                "Code mode requested but dependencies are missing (%s). "
                "Falling back to direct tool catalog. Install requirements with fastmcp[code-mode].",
                exc,
            )
    else:
        logger.info("Code mode disabled (CODE_MODE=false). Exposing direct tool catalog.")

    _log_capability_summary()
    return mcp
