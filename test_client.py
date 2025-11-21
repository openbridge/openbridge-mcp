#!/usr/bin/env python3
"""Test client for verifying MCP server authentication."""

import asyncio
import os
import httpx
from dotenv import load_dotenv

async def test_mcp_connection():
    """Test MCP server connection with authentication."""
    load_dotenv()

    refresh_token = os.getenv("OPENBRIDGE_REFRESH_TOKEN")
    if not refresh_token:
        print("❌ OPENBRIDGE_REFRESH_TOKEN not set in .env")
        return

    # MCP server URL (matching Claude Desktop config - Docker maps 8002:8000)
    url = "http://localhost:8002/mcp"

    # Headers matching Claude Desktop config
    headers = {
        "Authorization": f"Bearer {refresh_token}",
        "Accept": "application/json,text/event-stream",
        "Content-Type": "application/json",
    }

    # MCP initialize request
    initialize_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {}
            },
            "clientInfo": {
                "name": "test-client",
                "version": "1.0.0"
            }
        }
    }

    print(f"🔗 Connecting to {url}")
    print(f"🔑 Using token: {refresh_token[:20]}...{refresh_token[-10:]}")
    print(f"📤 Sending initialize request...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=initialize_request, headers=headers)

            print(f"\n📥 Response Status: {response.status_code}")
            print(f"📋 Response Headers: {dict(response.headers)}")

            # Extract session ID from headers
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                print(f"🆔 Session ID: {session_id}")
                headers["mcp-session-id"] = session_id

            print(f"📄 Response Body:")
            print(response.text)

            if response.status_code == 200:
                print("\n✅ Connection successful!")

                # Try listing tools
                tools_request = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                }

                print(f"\n📤 Requesting tools list...")
                tools_response = await client.post(url, json=tools_request, headers=headers)

                print(f"📥 Tools Response Status: {tools_response.status_code}")
                print(f"📄 Tools Response:")
                print(tools_response.text)

                if tools_response.status_code == 200:
                    tools_data = tools_response.json()
                    if "result" in tools_data and "tools" in tools_data["result"]:
                        tool_count = len(tools_data["result"]["tools"])
                        print(f"\n✅ Found {tool_count} tools")
                        for tool in tools_data["result"]["tools"][:5]:  # Show first 5
                            print(f"   - {tool.get('name', 'unknown')}")

            else:
                print(f"\n❌ Connection failed with status {response.status_code}")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_mcp_connection())
