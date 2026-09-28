import json
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from pydantic import BaseModel, ValidationError as PydanticValidationError

from src.server.error_envelope_middleware import ErrorEnvelopeMiddleware
from src.server.tools.base import raise_for_auth_status


def _ctx(name: str = "test_tool", method: str = "tools/call"):
    return SimpleNamespace(message=SimpleNamespace(name=name), method=method)


@pytest.mark.asyncio
async def test_keyerror_is_wrapped_as_internal_error():
    mw = ErrorEnvelopeMiddleware()

    async def call_next(_):
        raise KeyError("missing")

    with pytest.raises(ToolError) as exc_info:
        await mw.on_call_tool(_ctx("boom"), call_next)

    envelope = json.loads(str(exc_info.value))
    assert envelope["error_kind"] == "internal_error"
    assert envelope["_envelope_version"] == 1


@pytest.mark.asyncio
async def test_pydantic_validation_error_is_wrapped():
    mw = ErrorEnvelopeMiddleware()

    class Payload(BaseModel):
        value: int

    async def call_next(_):
        try:
            Payload(value="x")
        except PydanticValidationError as exc:
            raise exc
        raise AssertionError("expected ValidationError")

    with pytest.raises(ToolError) as exc_info:
        await mw.on_call_tool(_ctx("validated"), call_next)

    envelope = json.loads(str(exc_info.value))
    assert envelope["error_kind"] == "mcp_input_validation"
    assert envelope["error_code"] == "INPUT_VALIDATION_FAILED"
    assert envelope["details"][0]["received_type"] == "str"


@pytest.mark.asyncio
async def test_unknown_tool_not_found_is_wrapped():
    mw = ErrorEnvelopeMiddleware()

    async def call_next(_):
        raise NotFoundError("missing")

    with pytest.raises(ToolError) as exc_info:
        await mw.on_request(_ctx("missing_tool"), call_next)

    envelope = json.loads(str(exc_info.value))
    assert envelope["error_kind"] == "tool_not_found"
    assert envelope["error_code"] == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_authentication_error_is_body_safe_envelope_over_mcp_client():
    server = FastMCP("auth-envelope-test")
    server.add_middleware(ErrorEnvelopeMiddleware())

    @server.tool(name="auth_failure")
    async def auth_failure() -> None:
        raise_for_auth_status(
            SimpleNamespace(
                status_code=403,
                text="sensitive upstream body",
            ),
            tool="auth_failure",
            operation="testing authorization",
        )

    async with Client(server) as client:
        result = await client.call_tool(
            "auth_failure",
            raise_on_error=False,
        )

    assert result.is_error is True
    envelope = json.loads(result.content[0].text.removeprefix("ToolError: "))
    assert envelope["error_kind"] == "auth_error"
    assert envelope["error_code"] == "AUTHENTICATION_ERROR"
    assert envelope["tool"] == "auth_failure"
    assert "sensitive upstream body" not in result.content[0].text
