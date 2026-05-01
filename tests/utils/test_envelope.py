import json
from pathlib import Path

import pytest

from src.utils.envelope import ENVELOPE_VERSION, auth_error, make_error, not_found


jsonschema = pytest.importorskip("jsonschema")


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "error-envelope.schema.json"
ERROR_KINDS = [
    "mcp_input_validation",
    "tool_not_found",
    "sp_api_http",
    "ads_api_http",
    "sp_api_client",
    "ads_api_client",
    "auth_error",
    "rate_limited",
    "sandbox_runtime",
    "internal_error",
]


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("error_kind", ERROR_KINDS)
def test_make_error_validates_against_schema(error_kind):
    envelope = make_error(
        tool="test_tool",
        error_kind=error_kind,  # type: ignore[arg-type]
        summary="test summary",
        error_code="INTERNAL_ERROR",
        retryable=False,
    )
    jsonschema.validate(instance=envelope, schema=_schema())


def test_make_error_always_sets_envelope_version():
    envelope = make_error(
        tool="test_tool",
        error_kind="internal_error",
        summary="test summary",
        error_code="INTERNAL_ERROR",
        retryable=False,
    )
    assert envelope["_envelope_version"] == ENVELOPE_VERSION


def test_optional_fields_omitted_when_none():
    envelope = make_error(
        tool="test_tool",
        error_kind="internal_error",
        summary="test summary",
        error_code="INTERNAL_ERROR",
        retryable=False,
        meta=None,
        legacy_error_kind=None,
    )
    assert "_meta" not in envelope
    assert "legacy_error_kind" not in envelope


def test_shortcuts_return_v1_envelopes():
    nf = not_found(
        tool="get_subscription_by_id",
        resource_type="subscription",
        resource_id=1,
        error_code="SUBSCRIPTION_NOT_FOUND",
    )
    ae = auth_error(tool="get_healthchecks", summary="bad auth")

    assert nf["_envelope_version"] == 1
    assert ae["_envelope_version"] == 1
    assert nf["error_kind"] == "mcp_input_validation"
    assert ae["error_kind"] == "auth_error"
