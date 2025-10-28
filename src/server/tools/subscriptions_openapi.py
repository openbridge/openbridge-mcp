import os
import httpx
from fastmcp import FastMCP
import yaml
import asyncio
from src.utils.http import http_client_manager
from src.utils.http_client import AuthenticatedClient


class SubscriptionsOpenAPI(AuthenticatedClient):
    def __init__(self, auth_manager=None):
        # Create an HTTP client for your API
        self.client = AuthenticatedClient(
            base_url=os.getenv("SUBSCRIPTIONS_API_ENDPOINT", "https://subscriptions.api.openbridge.io"),
            headers={"User-Agent": "Openbridge-MCP/1.0"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            auth_manager=auth_manager,
        )
        http_client_manager.register_external_client(self.client)

        # Load your OpenAPI spec
        openapi_spec = yaml.safe_load(httpx.get(f"{os.getenv('SUBSCRIPTIONS_API_ENDPOINT', 'https://subscriptions.api.openbridge.io')}/api/schema.yaml").text)


        # Create the MCP server
        mcp = FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=self.client,
            name="Subscriptions API MCP Server"
        )
        self.mcp = mcp
        self.init_tools()

    async def register_tools(self):
        tools = await self.mcp.get_tools()
        return tools

    def init_tools(self):
        tools = asyncio.run(self.register_tools())
        self.tools = tools
