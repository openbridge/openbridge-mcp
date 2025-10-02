import asyncio
from mcp_agent.core.fastagent import FastAgent


fast = FastAgent(name="openbridge-mcp-agent")

@fast.agent(
    instruction="""You are an agent that helps with Openbridge MCP tasks.
    Use your available tools to assist users with their requests about their Openbridge resources.""",
    servers=["openbridge"],
)
async def main():
    # Local call
    # async with fast.run() as agent:
    #     await agent.send('***CALL_TOOL get_remote_identities')
    # MCP server (for use with Claude)
    await fast.start_server(
        port=8001,
        server_name="Openbridge MCP Agent Server",
        server_description="This server handles requests for the Openbridge MCP agent.",
    )


if __name__ == "__main__":
    asyncio.run(main())
