#!/usr/bin/env python3
"""Test calling an MCP tool that requires authentication."""

import asyncio
import os
import httpx
import json
from dotenv import load_dotenv

async def test_tool_call():
    """Test calling get_remote_identities tool."""
    load_dotenv()

    refresh_token = os.getenv("OPENBRIDGE_REFRESH_TOKEN")
    url = "http://localhost:8002/mcp"

    headers = {
        "Authorization": f"Bearer {refresh_token}",
        "Accept": "application/json,text/event-stream",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Initialize session
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "test-client", "version": "1.0.0"}
            }
        }

        print("🔗 Initializing MCP session...")
        response = await client.post(url, json=init_request, headers=headers)
        session_id = response.headers.get("mcp-session-id")
        headers["mcp-session-id"] = session_id
        print(f"✅ Session initialized: {session_id}\n")

        # Call get_remote_identities tool
        tool_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_remote_identities",
                "arguments": {}
            }
        }

        print("📞 Calling get_remote_identities tool...")
        tool_response = await client.post(url, json=tool_request, headers=headers)

        print(f"📥 Status: {tool_response.status_code}")

        # Parse SSE format
        text = tool_response.text
        for line in text.split('\n'):
            if line.startswith('data: '):
                data = json.loads(line[6:])
                if "result" in data:
                    content = data["result"]["content"]
                    if isinstance(content, list) and len(content) > 0:
                        result = json.loads(content[0]["text"])
                        if "result" in result:
                            identities = result["result"]
                            print(f"\n✅ Found {len(identities)} remote identities:")
                            for identity in identities[:5]:  # Show first 5
                                print(f"   - {identity.get('name', 'N/A')} (ID: {identity.get('id', 'N/A')})")
                            if len(identities) > 5:
                                print(f"   ... and {len(identities) - 5} more")
                        else:
                            print(f"\n❌ API Error: {result}")
                elif "error" in data:
                    print(f"\n❌ Error: {data['error']}")

if __name__ == "__main__":
    asyncio.run(test_tool_call())
