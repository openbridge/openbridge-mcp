"""Entry point for the MCP Query Execution server."""

import os
import sys

from dotenv import load_dotenv

from src.server.mcp_server import create_mcp_server
from src.utils.logging import get_logger

logger = get_logger("main")


def _stateless_http_enabled() -> bool:
    """Resolve MCP_STATELESS_HTTP into a boolean.

    Defaults to True so multi-instance deployments behind an L7 LB without
    sticky sessions are safe out of the box. Set ``MCP_STATELESS_HTTP=false``
    to keep FastMCP's stateful HTTP session behavior (only safe when the
    deployment guarantees session affinity).
    """
    raw = os.getenv("MCP_STATELESS_HTTP")
    if raw is None:
        return True
    normalized = raw.strip().lower()
    if normalized in {"false", "0", "no", "off"}:
        return False
    if normalized in {"true", "1", "yes", "on"}:
        return True
    logger.warning(
        "MCP_STATELESS_HTTP=%r is not a recognized boolean; defaulting to True",
        raw,
    )
    return True


def main():
    """Main entry point."""
    try:
        # Load environment variables
        env_path = '.env'
        load_dotenv(env_path)
        MCP_PORT = int(os.getenv('MCP_PORT', 8000))
        MCP_HOST = os.getenv('MCP_HOST', '0.0.0.0')
        stateless_http = _stateless_http_enabled()

        # Create and run MCP server
        server = create_mcp_server()
        logger.info(
            "Starting MCP server with HTTP transport (stateless_http=%s)",
            stateless_http,
        )
        server.run(
            transport="http",
            host=MCP_HOST,
            port=MCP_PORT,
            stateless_http=stateless_http,
        )

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
