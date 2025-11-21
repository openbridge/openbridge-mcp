import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.responses import JSONResponse

load_dotenv('./.env')

# Imports below must come after load_dotenv() to ensure env vars are loaded
from src.server.tools import remote_identity as remote_identity_tools  # noqa: E402
from src.server.tools import service as service_tools  # noqa: E402
from src.server.tools import healthchecks as healthchecks_tools  # noqa: E402
from src.server.tools import jobs as jobs_tools  # noqa: E402
from src.server.tools import products as products_tools  # noqa: E402
from src.server.tools import subscriptions as subscriptions_tools  # noqa: E402
from src.utils.logging import get_logger  # noqa: E402
from src.server.sampling import create_sampling_handler  # noqa: E402


logger = get_logger("mcp_server")

def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server."""
    sampling_handler = create_sampling_handler()

    # Initialize FastMCP server
    mcp = FastMCP(
        name="Openbridge MCP",
        instructions="Openbridge MCP server for utilizing a variety of API endpoints and tools.",
        sampling_handler=sampling_handler,
    )
    # Register tools
    # Remote identity tools
    mcp.tool(
        name='get_remote_identities',
        description='Retrieve remote identities for the current user. Returns list of remote identity names.',
    )(remote_identity_tools.get_remote_identities)
    mcp.tool(
        name='get_remote_identity_by_id',
        description='Retrieve a specific remote identity by its ID. Returns remote identity details if found.',
    )(remote_identity_tools.get_remote_identity_by_id)
    # Service Tools
    # Query validation tools require an API key for LLM sampling
    has_sampling_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if has_sampling_key:
        mcp.tool(
            name='validate_query',
            description='Analyze SQL with sampling, requiring a LIMIT unless allow_unbounded=True.',
        )(service_tools.validate_query)
        mcp.tool(
            name='execute_query',
            description='Validate then execute a SQL query; defaults to requiring LIMIT unless allow_unbounded=True.',
        )(service_tools.execute_query)
    else:
        logger.info("Skipping SQL query tools: no API key configured (set FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY)")
    mcp.tool(
        name='get_amazon_api_access_token',
        description='Get the Amazon API access token for a given remote identity ID. Returns the access token if available.',
    )(service_tools.get_amazon_api_access_token)
    mcp.tool(
        name='get_amazon_advertising_profiles',
        description='List the Amazon Advertising profiles for a given remote identity ID. Returns a list of profiles.',
    )(service_tools.get_amazon_advertising_profiles)
    mcp.tool(
        name='get_table_schema',
        description='Get the schema/rules for a given table name from the rules API. Use table names from list_product_tables output. Returns the schema if found.',
    )(service_tools.get_table_schema)
    mcp.tool(
        name='get_suggested_table_names',
        description="""
    Given a query string, obtain a list of possible table names from the rules API (through the service API).

    Args:
        query (str): The SQL query to analyze.
    Returns:
        Optional[List[str]] | str: A list of possible table names found from the query, or an error message if an invalid key is specified.
    """,
    )(service_tools.get_suggested_table_names)
    # Healthchecks Tools
    mcp.tool(
        name='get_healthchecks',
        description='Get the healthchecks related to the current user. Returns a list of healthchecks.',
    )(healthchecks_tools.get_healthchecks)
    # Jobs tools
    mcp.tool(
        name='get_jobs',
        description='Fetch jobs from the Openbridge API.',
    )(jobs_tools.get_jobs)
    mcp.tool(
        name='create_job',
        description='Create a job for a given subscription.',
    )(jobs_tools.create_job)
    # Subscriptions tools
    mcp.tool(
        name='get_subscriptions',
        description='Get the subscriptions related to the current user. Returns a list of subscriptions.',
    )(subscriptions_tools.get_subscriptions)
    mcp.tool(
        name='get_storage_subscriptions',
        description='Get the storage subscriptions related to the current user. Returns a list of storage subscriptions.',
    )(subscriptions_tools.get_storage_subscriptions)
    # Products tools
    mcp.tool(
        name='get_product_stage_ids',
        description='Get the stage IDs for a specific product. Returns a list of stage IDs associated with the product.',
    )(products_tools.get_product_stage_ids)
    mcp.tool(
        name='search_products',
        description='''Search for Openbridge data products by name (fuzzy matching with ranking).

Use broad search terms for best results - partial matches are ranked by relevance.
Examples: "Amazon Ads", "Google Analytics", "Facebook", "Sponsored Products"

Returns: List of matching products with id, name, and worker_name.
Next step: Use product id with list_product_tables to see available tables.''',
    )(products_tools.search_products)
    mcp.tool(
        name='list_product_tables',
        description='''List all tables (data payloads) available for a specific product.

Args:
  - product_id: Product ID from search_products (required)
  - subscription_id: Optional - filters tables to only those enabled for this subscription

Returns: List of table objects with name, stage_id, and id fields.
Next step: Use table name with get_table_schema to see column details and rules.

Example workflow: search_products("Amazon Ads") → list_product_tables(50) → get_table_schema("amzn_ads_sp_campaigns")''',
    )(products_tools.list_product_tables)

    # Health check endpoint for monitoring and load balancers
    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request):
        """Health check endpoint for monitoring, load balancers, and deployment platforms."""
        return JSONResponse({
            "status": "healthy",
            "service": "openbridge-mcp",
            "version": "1.0.0"
        })

    return mcp
