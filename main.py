"""Entry point for the MCP Query Execution server."""

import os
import sys

import uvicorn
from dotenv import load_dotenv

from src.auth.path_token_middleware import PathTokenMiddleware, load_secret
from src.server.mcp_server import create_mcp_server
from src.utils.logging import get_logger
from src.utils.runtime_security import (
    path_tokens_enabled,
    positive_int_env,
    validate_runtime_security,
)

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
        mcp_port = int(os.getenv('MCP_PORT', 8000))
        mcp_host = os.getenv('MCP_HOST', '0.0.0.0')
        validate_runtime_security(mcp_host)
        stateless_http = _stateless_http_enabled()
        limit_concurrency = positive_int_env("MCP_LIMIT_CONCURRENCY", default=100)

        # Create and run MCP server
        server = create_mcp_server()
        logger.info(
            "Starting MCP server with HTTP transport (stateless_http=%s)",
            stateless_http,
        )
        app = server.http_app(stateless_http=stateless_http)
        if path_tokens_enabled():
            # Path-token handling must remain outside Starlette routing.
            app = PathTokenMiddleware(app, secret=load_secret())
        uvicorn.run(
            app,
            host=mcp_host,
            port=mcp_port,
            lifespan="on",
            limit_concurrency=limit_concurrency,
        )

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
