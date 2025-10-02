import httpx
from fastmcp import FastMCP
import yaml
import asyncio

# Create an HTTP client for your API
client = httpx.AsyncClient(base_url="https://authentication.api.dev.openbridge.io")

# Load your OpenAPI spec
openapi_spec = yaml.safe_load(httpx.get("https://authentication.api.dev.openbridge.io/api/schema.yaml").text)


# Create the MCP server
mcp = FastMCP.from_openapi(
    openapi_spec=openapi_spec,
    client=client,
    name="Account API MCP Server"
)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8055)

# Collect all tools from the MCP
# mcp.get_tools is async, so we need to run it in an async context
async def register_tools():
    tools = await mcp.get_tools()
    return tools

tools = asyncio.run(register_tools())
