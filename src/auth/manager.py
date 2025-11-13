"""Minimal authentication manager facade for Openbridge MCP.

The historic codebase included a large Amazon Ads authentication stack with
multiple providers, persistent token stores, and credential switching.  The
deployment model for this project only needs Openbridge refresh-token
conversion, so we collapse the surface area to a lightweight helper that
returns a singleton ``OpenbridgeAuth`` instance.
"""

from __future__ import annotations

from typing import Optional

from .simple import OpenbridgeAuth, get_auth

# Global cache so legacy callers expecting a long-lived "manager" still work.
_auth_manager: Optional[OpenbridgeAuth] = None


def get_auth_manager() -> OpenbridgeAuth:
    """Return the singleton Openbridge authentication helper.

    Historically this function returned a complex ``AuthManager`` object.  The
    simplified implementation keeps the same entrypoint but delegates to the
    minimal ``OpenbridgeAuth`` helper so existing imports keep working.
    """
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = get_auth()
    return _auth_manager
