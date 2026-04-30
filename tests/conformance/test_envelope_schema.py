"""Validate captured fixtures against the v1 envelope JSON Schema.

Failure modes:

- ``test_schema_loads`` — the schema file itself is invalid JSON Schema.
- ``test_envelope_fixtures_validate`` — a captured error envelope no longer
  matches the contract; either the fixture is wrong or the contract changed.
- ``test_response_fixtures_meta_normalized_validates`` — a captured success
  response carries a ``_meta.normalized`` block that violates the contract.

When a fixture fails: investigate first, do not just edit the fixture. The
fixture represents a behavior that some server actually emits today.
"""

from __future__ import annotations

import pytest

jsonschema = pytest.importorskip("jsonschema")


def test_schema_loads(envelope_schema: dict) -> None:
    """The schema document is itself a valid Draft 2020-12 JSON Schema."""
    validator_cls = jsonschema.validators.validator_for(envelope_schema)
    validator_cls.check_schema(envelope_schema)


def test_envelope_fixtures_validate(envelope_schema: dict, envelope_fixtures) -> None:
    """Every captured error envelope validates against the schema."""
    if not envelope_fixtures:
        pytest.skip("No envelope fixtures captured yet.")
    for name, envelope in envelope_fixtures:
        try:
            jsonschema.validate(instance=envelope, schema=envelope_schema)
        except jsonschema.ValidationError as exc:
            raise AssertionError(
                f"Fixture {name!r} failed envelope schema validation: {exc.message}\n"
                f"Path: {list(exc.absolute_path)}"
            ) from exc


def test_response_fixtures_meta_normalized_validates(
    envelope_schema: dict, response_fixtures
) -> None:
    """Success-response fixtures that include ``_meta.normalized`` validate
    against the same schema's ``_meta.normalized`` rules.

    We construct a minimal valid envelope wrapper for each response so the
    same schema validates the ``_meta.normalized`` shape regardless of which
    side of the response (success vs. error) it is attached to.
    """
    if not response_fixtures:
        pytest.skip("No response fixtures captured yet.")
    for name, response in response_fixtures:
        meta = response.get("_meta", {})
        if not meta.get("normalized"):
            continue
        wrapped = {
            "error_kind": "internal_error",
            "tool": "_conformance_wrapper",
            "summary": "Wrapper for validating success-path _meta.normalized.",
            "details": [],
            "hints": [],
            "examples": [],
            "error_code": "CONFORMANCE_WRAPPER",
            "retryable": False,
            "_envelope_version": 1,
            "_meta": {"normalized": meta["normalized"]},
        }
        try:
            jsonschema.validate(instance=wrapped, schema=envelope_schema)
        except jsonschema.ValidationError as exc:
            raise AssertionError(
                f"Response fixture {name!r} _meta.normalized failed: {exc.message}\n"
                f"Path: {list(exc.absolute_path)}"
            ) from exc


def test_error_kind_taxonomy_is_closed(envelope_schema: dict) -> None:
    """Schema declares error_kind as a closed enum (no ``string`` fallback).

    The expected set tracks the v1 taxonomy in ``CONTRACT.md`` ``## error_kind
    taxonomy (v1)``. ``tool_not_found`` is a Round 12 additive entry; if you
    add another Round-N entry to the contract, update this set in the same PR.
    """
    error_kind = envelope_schema["properties"]["error_kind"]
    assert "enum" in error_kind, "error_kind must be a closed enum"
    expected_v1 = {
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
    }
    assert set(error_kind["enum"]) == expected_v1, (
        f"error_kind enum drifted from v1 contract. "
        f"Got {set(error_kind['enum'])}, expected {expected_v1}."
    )


def test_normalized_kinds_are_closed(envelope_schema: dict) -> None:
    """Schema declares _meta.normalized.kind as a closed enum.

    Tracks ``CONTRACT.md`` ``### _meta.normalized`` ``kind`` table.
    ``unknown_field_rejected`` is a Round 12 follow-up to label events when
    ``MCP_STRICT_UNKNOWN_FIELDS=true`` (the default).
    """
    normalized_items = (
        envelope_schema["properties"]["_meta"]["properties"]["normalized"]["items"]
    )
    kind = normalized_items["properties"]["kind"]
    assert "enum" in kind
    expected_v1 = {
        "renamed",
        "dropped_alias",
        "coerced",
        "unknown_field_passed_through",
        "unknown_field_rejected",
    }
    assert set(kind["enum"]) == expected_v1, (
        f"_meta.normalized.kind enum drifted from v1 contract. "
        f"Got {set(kind['enum'])}, expected {expected_v1}."
    )
