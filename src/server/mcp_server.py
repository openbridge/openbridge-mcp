from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv('./.env')

from src.server.tools.base import get_auth_headers
from src.server.tools import remote_identity as remote_identity_tools
from src.server.tools import service as service_tools
from src.server.tools import healthchecks as healthchecks_tools
from src.server.tools import jobs as jobs_tools
from src.server.tools import products as products_tools
from src.server.tools import subscriptions as subscriptions_tools
from src.utils.logging import get_logger
from src.server.tools.account_openapi import AccountOpenAPI
from src.server.tools.subscriptions_openapi import SubscriptionsOpenAPI
from src.auth.authentication import create_auth_middleware, create_openbridge_config
from src.auth.manager import get_auth_manager


logger = get_logger("mcp_server")

def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server."""
    # Create middleware stack

    # Configure JWT middleware
    auth_cfg = create_openbridge_config()
    auth_manager = get_auth_manager()
    middleware = create_auth_middleware(auth_cfg, jwt_middleware=False, auth_manager=auth_manager)

    # Initialize FastMCP server
    mcp = FastMCP(
        name="Openbridge MCP",
        instructions="Openbridge MCP server for utilizing a variety of API endpoints and tools.",
        middleware=middleware,
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
    mcp.tool(
        name='execute_query',
        description='Execute a SQL query in the query API and return the results. Requires a SQL query and a key name for the account mapping.',
    )(service_tools.execute_query)
    mcp.tool(
        name='get_amazon_api_access_token',
        description='Get the Amazon API access token for a given remote identity ID. Returns the access token if available.',
    )(service_tools.get_amazon_api_access_token)
    mcp.tool(
        name='get_amazon_advertising_profiles',
        description='List the Amazon Advertising profiles for a given remote identity ID. Returns a list of profiles.',
    )(service_tools.get_amazon_advertising_profiles)
    mcp.tool(
        name='get_table_rules',
        description='Get the rules for a given table name from the rules API. Returns the rules if found.',
    )(service_tools.get_table_rules)
    mcp.tool(
        name='get_suggested_table_names',
        description="""
    Given a query string, obtain a list of possible table names from the rules API (through the service API).

    INSTRUCTIONS: Based on a user's request, determine the best query key to use.
    * If the user makes a request related to "amazon ads sponsored products", use the key "amazon-ads/amzn_ads_sp"
    * If the user makes a request related to "amazon ads sponsored brands", use the key "amazon-ads/amzn_ads_sb"
    * If the user makes a request related to "amazon ads sponsored display", use the key "amazon-ads/amzn_ads_sd"
    * If the user makes a request related to "Amazon DSP", use the key "amazon-dsp/"
    * If the user makes *any* request which doesn't fit into the above categories, deny the request and assert that only the specified keys are allowed.

    This tool will *ONLY* allow the above keys. Do not invent or create any new keys. 

    The response should be inspected for an error message to give the client an opportunity to fix the issue.

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
        name='create_oneoff_jobs',
        description='Create one-off jobs for a given subscription.',
    )(jobs_tools.create_oneoff_jobs)
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
    # Account Tools
    # account_openapi = AccountOpenAPI(auth_manager=auth_manager)
    # # Register all functions from the account_openapi module as tools
    # for tool_name, tool in account_openapi.tools.items():
    #     print(f"Registering tool: {tool_name}")
    #     mcp.add_tool(tool)
    # subscriptions_openapi = SubscriptionsOpenAPI(auth_manager=auth_manager)
    # # Register all functions from the subscriptions_openapi module as tools
    # for tool_name, tool in subscriptions_openapi.tools.items():
    #     print(f"Registering tool: {tool_name}")
    #     mcp.add_tool(tool)
    return mcp
