"""Per-request session state exposed via ``contextvars``.

The authentication middleware writes the resolved JWT for the current
request into a ContextVar. This makes the token visible to synchronous
helpers (like ``get_auth_headers``) regardless of which FastMCP
``Context`` instance they happen to be handed — which matters when Code
Mode's sandbox invokes tools with a fresh context that did not see the
outer request's ``set_state`` calls.

ContextVars propagate across ``await`` boundaries within the same
asyncio task, so values set in middleware remain visible to tool
functions executed under the same request task.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_jwt_var: ContextVar[Optional[str]] = ContextVar("openbridge_jwt", default=None)


def set_request_jwt(token: Optional[str]) -> None:
    """Store the resolved JWT for the current async task."""
    _jwt_var.set(token)


def get_request_jwt() -> Optional[str]:
    """Return the JWT set for the current async task, if any."""
    return _jwt_var.get()


__all__ = ["set_request_jwt", "get_request_jwt"]
