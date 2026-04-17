"""Entry point for the MCP Query Execution server."""

import os
import sys

from dotenv import load_dotenv

from src.server.mcp_server import create_mcp_server
from src.utils.logging import get_logger

logger = get_logger("main")

def main():
    """Main entry point."""
    try:
        # Load environment variables
        env_path = '.env'
        load_dotenv(env_path)
        MCP_PORT = int(os.getenv('MCP_PORT', 8000))
        MCP_HOST = os.getenv('MCP_HOST', '0.0.0.0')

        # Create and run MCP server
        server = create_mcp_server()
        logger.info("Starting MCP server with HTTP transport")
        server.run(transport="http", host=MCP_HOST, port=MCP_PORT)

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
