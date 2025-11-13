import httpx
from fastmcp import FastMCP
from src.utils.logging import get_logger
import asyncio
import yaml
import os
from src.auth.authentication import create_auth_middleware, create_openbridge_config
from src.auth.manager import get_auth_manager

logger = get_logger("authentication")

# Create an HTTP client for your API
client = httpx.AsyncClient(base_url="http://localhost:8101")

# Load your OpenAPI spec
openapi_spec = httpx.get("http://localhost:8101/api/schema.yaml")
# Convert from YAML to JSON
openapi_spec = yaml.safe_load(openapi_spec.text)
print(openapi_spec)

# Enable experimental OpenAPI MCP processing
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

# Create middleware stack
auth_cfg = create_openbridge_config()
auth_manager = get_auth_manager()
middleware = create_auth_middleware(auth_cfg, jwt_middleware=False, auth_manager=auth_manager)

# Create the MCP server
mcp = FastMCP.from_openapi(
    openapi_spec=openapi_spec,
    client=client,
    name="Authentication API MCP Server"
)
# Register middleware
for mw in middleware:
    mcp.add_middleware(mw)
    logger.info("Openbridge authentication middleware enabled")

# Collect all tools from the MCP
# mcp.get_tools is async, so we need to run it in an async context
async def register_tools():
    tools = await mcp.get_tools()
    return tools

tools = asyncio.run(register_tools())
