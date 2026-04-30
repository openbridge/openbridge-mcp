"""Cross-server v1 envelope contract tests.

Parametrized over both Amazon SP MCP and Amazon Ads MCP so every
assertion runs against both. A failure on either side fails the suite.

This is the structural acceptance test for the cross-server work: one
agent-side error handler must work against both servers. If the servers
diverge on shape, taxonomy, or hint quality, every cross-server agent
has to fork — these tests pin that property in CI.

Setup
-----

Requires two MCP clients exposed as fixtures ``sp_client`` and
``ads_client``. Each client must implement:

  - ``call_tool(name, params) -> dict``
  - ``call_tool_expecting_error(name, params) -> dict``  (parsed envelope)
  - ``call_paginate_with_pascalcase() -> dict``  (success response)
  - ``call_known_rate_limited_read() -> dict``  (success response)
  - ``call_tool_expecting_upstream_error() -> dict``  (envelope)
  - ``try_trigger_rate_limit_or_skip() -> dict | None``
  - ``clear_active_identity()``
  - ``valid_set_context_args() -> dict``

The conftest stubs at the bottom of this file describe the contract
each per-server fixture must honor. Implement them under
``conftest.py`` in this directory.

Tests are skipped when ``sp_client`` / ``ads_client`` fixtures aren't
provided — a fresh checkout that hasn't wired up live MCP clients can
still collect this file without errors.

Run policy
----------

- Run on every PR that touches envelope/hint code in either server.
- Run nightly against staging.
- Block merge if any test fails. Contract is "v1"; breaking it
  requires bumping ``_envelope_version`` and is a coordinated change.
"""

from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Shared constants — the v1 contract
# ---------------------------------------------------------------------------

ENVELOPE_VERSION = 1

VALID_ERROR_KINDS = {
    "mcp_input_validation",
    "tool_not_found",
    "sp_api_http",
    "sp_api_client",
    "ads_api_http",
    "ads_api_client",
    "rate_limited",
    "auth_error",
    "sandbox_runtime",
    "internal_error",
}

REQUIRED_ENVELOPE_KEYS = {
    "error_kind",
    "tool",
    "summary",
    "details",
    "hints",
    "examples",
    "error_code",
    "retryable",
    "_envelope_version",
}


# ---------------------------------------------------------------------------
# Server parametrization
# ---------------------------------------------------------------------------

SERVERS = ["sp_client", "ads_client"]


@pytest.fixture(params=SERVERS)
def server(request):
    """Yields each server's client in turn so every test runs against both.

    Skips gracefully when the per-server fixture isn't configured (e.g.
    a fresh checkout running the suite without live MCP endpoints).
    """
    name = request.param
    try:
        return request.getfixturevalue(name)
    except pytest.FixtureLookupError:
        pytest.skip(f"{name} fixture not configured in conftest.py")


# ---------------------------------------------------------------------------
# Section 0 — Deploy version probe (run first; diagnoses stale containers)
# ---------------------------------------------------------------------------


class TestDeployVersionProbe:
    """If these fail, the deployed container doesn't have the v1 work
    and every other test below is testing the wrong build."""

    def test_get_envelope_contract_returns_v1(self, server):
        result = server.call_tool("get_envelope_contract", {})
        assert result.get("contract_version") == ENVELOPE_VERSION, (
            f"Server reports contract_version={result.get('contract_version')!r}. "
            f"Expected {ENVELOPE_VERSION}. The deployed container does NOT "
            f"have the v1 envelope work; skip / fix the deploy before "
            f"diagnosing other gaps."
        )

    def test_get_envelope_contract_lists_supported_kinds(self, server):
        result = server.call_tool("get_envelope_contract", {})
        kinds = set(result.get("error_kinds") or [])
        assert kinds, "server must advertise a non-empty error_kinds list"
        assert kinds.issubset(VALID_ERROR_KINDS), (
            f"server advertises unknown error_kind values: "
            f"{kinds - VALID_ERROR_KINDS}"
        )


# ---------------------------------------------------------------------------
# Section 1 — Envelope shape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    def test_envelope_has_all_required_keys(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        missing = REQUIRED_ENVELOPE_KEYS - env.keys()
        assert not missing, (
            f"Envelope missing required keys: {missing}. "
            f"Got: {sorted(env.keys())}"
        )

    def test_envelope_version_is_1(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert env["_envelope_version"] == ENVELOPE_VERSION

    def test_error_kind_is_in_taxonomy(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert env["error_kind"] in VALID_ERROR_KINDS, (
            f"error_kind={env['error_kind']!r} not in taxonomy "
            f"{VALID_ERROR_KINDS}"
        )

    def test_retryable_is_bool(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert isinstance(env["retryable"], bool)

    def test_hints_is_list_of_strings(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert isinstance(env["hints"], list)
        assert all(isinstance(h, str) for h in env["hints"])

    def test_details_is_list_of_dicts(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert isinstance(env["details"], list)
        for d in env["details"]:
            assert isinstance(d, dict)
            assert "path" in d and "issue" in d and "received_type" in d


# ---------------------------------------------------------------------------
# Section 2 — error_kind attribution
# ---------------------------------------------------------------------------


class TestErrorKindAttribution:
    def test_internal_validation_is_mcp_input_validation(self, server):
        """Internal-only lookup with bad input must NOT be tagged as
        upstream HTTP. Amazon never sees this call."""
        env = server.call_tool_expecting_error(
            "list_identities", {"identity_type": "999"}
        )
        assert env["error_kind"] == "mcp_input_validation", (
            f"list_identities with bad identity_type tagged "
            f"{env['error_kind']!r}; expected mcp_input_validation."
        )

    def test_missing_required_field_is_mcp_input_validation(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        assert env["error_kind"] == "mcp_input_validation"

    def test_bad_enum_is_mcp_input_validation(self, server):
        env = server.call_tool_expecting_error(
            "set_region", {"region": "antarctica"}
        )
        assert env["error_kind"] == "mcp_input_validation"


# ---------------------------------------------------------------------------
# Section 3 — Hint quality
# ---------------------------------------------------------------------------


class TestHintQuality:
    def test_missing_required_hint_names_the_field(self, server):
        env = server.call_tool_expecting_error("set_active_identity", {})
        hints_joined = " ".join(env["hints"])
        assert "identity_id" in hints_joined, (
            f"Missing-required hint did not name the field. "
            f"Hints: {env['hints']!r}"
        )

    def test_bad_enum_hint_lists_valid_values(self, server):
        env = server.call_tool_expecting_error(
            "set_region", {"region": "antarctica"}
        )
        hints_joined = " ".join(env["hints"])
        for region in ("na", "eu", "fe"):
            assert region in hints_joined, (
                f"Bad-enum hint missing canonical value {region!r}. "
                f"Hints: {env['hints']!r}"
            )

    @pytest.mark.parametrize("trailing", [";", " ", "\t"])
    def test_field_names_in_hints_have_no_trailing_artifacts(
        self, server, trailing
    ):
        """SP-API has been observed returning paths like 'reportTypes;'.
        Hints must strip these before formatting."""
        env = server.call_tool_expecting_error("set_active_identity", {})
        for hint in env["hints"]:
            for quoted in re.findall(r"'([^']+)'", hint):
                assert not quoted.endswith(trailing), (
                    f"Quoted field name in hint has trailing "
                    f"{trailing!r}: {quoted!r} in hint {hint!r}"
                )


# ---------------------------------------------------------------------------
# Section 4 — Pre-flight normalization
# ---------------------------------------------------------------------------


class TestPreflightNormalization:
    def test_pascalcase_normalization_emits_meta(self, server):
        result = server.call_paginate_with_pascalcase()
        assert "_meta" in result, (
            f"Successful normalization must include _meta. "
            f"Got: {sorted(result.keys())}"
        )
        normalized = result["_meta"].get("normalized")
        assert isinstance(normalized, list) and normalized
        for entry in normalized:
            assert "kind" in entry
            if entry["kind"] in ("renamed", "dropped_alias"):
                assert "from" in entry and "to" in entry


# ---------------------------------------------------------------------------
# Section 5 — _meta surface
# ---------------------------------------------------------------------------


class TestMetaSurface:
    def test_rate_limit_on_successful_response(self, server):
        result = server.call_known_rate_limited_read()
        assert "_meta" in result, (
            f"Successful response must carry _meta. "
            f"Got: {sorted(result.keys())}"
        )
        rl = result["_meta"].get("rate_limit")
        assert rl is not None, (
            f"_meta.rate_limit must be present on successful upstream "
            f"calls. _meta: {result['_meta']!r}"
        )

    def test_rate_limit_on_error_response(self, server):
        env = server.call_tool_expecting_upstream_error()
        if env["error_kind"] in {"sp_api_http", "ads_api_http", "rate_limited"}:
            assert "_meta" in env, (
                f"Upstream error envelope must carry _meta. "
                f"Got: {sorted(env.keys())}"
            )

    def test_retry_after_on_rate_limited(self, server):
        env = server.try_trigger_rate_limit_or_skip()
        if env is None:
            pytest.skip("Could not trigger rate_limited deterministically")
        assert env["error_kind"] == "rate_limited"
        assert env["retryable"] is True
        meta = env.get("_meta") or {}
        assert "retry_after_seconds" in meta or "retry_after_seconds" in env


# ---------------------------------------------------------------------------
# Section 6 — Cross-server uniformity
# ---------------------------------------------------------------------------


class TestCrossServerUniformity:
    """Same logical condition produces equivalent envelopes on both
    servers. ``error_code`` parity is asserted only for the
    ``mcp_input_validation`` kind where both servers emit the same
    shared code (``INPUT_VALIDATION_FAILED``); upstream HTTP codes
    keep per-server prefixes intentionally to preserve the
    boundary distinction (SP-API vs Ads-API vs Openbridge)."""

    def _both_clients(self, request):
        try:
            sp = request.getfixturevalue("sp_client")
            ads = request.getfixturevalue("ads_client")
        except pytest.FixtureLookupError as exc:
            pytest.skip(f"client fixture missing: {exc}")
        return sp, ads

    def test_input_validation_error_code_matches(self, request):
        sp, ads = self._both_clients(request)
        sp_env = sp.call_tool_expecting_error("set_active_identity", {})
        ads_env = ads.call_tool_expecting_error("set_active_identity", {})
        assert sp_env["error_kind"] == ads_env["error_kind"] == "mcp_input_validation"
        assert sp_env["error_code"] == ads_env["error_code"] == "INPUT_VALIDATION_FAILED"

    def test_envelope_keys_match_for_equivalent_failures(self, request):
        sp, ads = self._both_clients(request)
        sp_env = sp.call_tool_expecting_error("set_active_identity", {})
        ads_env = ads.call_tool_expecting_error("set_active_identity", {})
        # Both must include the v1 required keys; vendor-specific ones
        # are allowed but the v1 surface must match.
        sp_v1 = REQUIRED_ENVELOPE_KEYS & sp_env.keys()
        ads_v1 = REQUIRED_ENVELOPE_KEYS & ads_env.keys()
        assert sp_v1 == REQUIRED_ENVELOPE_KEYS == ads_v1, (
            f"v1 keys differ. SP missing: "
            f"{REQUIRED_ENVELOPE_KEYS - sp_v1}; "
            f"Ads missing: {REQUIRED_ENVELOPE_KEYS - ads_v1}"
        )

    def test_one_recovery_handler_works_against_both(self, request):
        """Acceptance test: a single error handler produces correct
        recovery decisions on both servers' envelopes."""
        sp, ads = self._both_clients(request)

        def decide(envelope: dict) -> str:
            kind = envelope["error_kind"]
            if kind == "mcp_input_validation":
                return "fix_input"
            if kind == "rate_limited":
                return "backoff"
            if kind == "auth_error":
                return "reauth"
            if kind in {"sp_api_http", "ads_api_http", "sp_api_client", "ads_api_client"}:
                return "investigate_upstream"
            return "abort"

        sp_env = sp.call_tool_expecting_error("set_active_identity", {})
        ads_env = ads.call_tool_expecting_error("set_active_identity", {})
        assert decide(sp_env) == decide(ads_env) == "fix_input"


# ---------------------------------------------------------------------------
# Conftest stub — implement these in conftest.py
# ---------------------------------------------------------------------------
#
# import pytest
# from your_test_clients import SPClient, AdsClient
#
# @pytest.fixture
# def sp_client():
#     c = SPClient(...)
#     c.call_tool("set_context", {"identity_id": "TEST_SP", "region": "na"})
#     yield c
#     c.cleanup()
#
# @pytest.fixture
# def ads_client():
#     c = AdsClient(...)
#     c.call_tool("set_context", {"identity_id": "TEST_ADS", "region": "na"})
#     yield c
#     c.cleanup()
#
# Each client implements:
#   .call_tool(name, params) -> dict
#   .call_tool_expecting_error(name, params) -> dict
#   .call_paginate_with_pascalcase() -> dict
#       SP: hit ``finances_listTransactions`` with PascalCase keys
#       Ads: hit ``page_profiles`` with {"Limit": 10, "Offset": 0}
#   .call_known_rate_limited_read() -> dict
#       SP: hit ``reports_getReports`` (well-defined SP-API rate limits)
#       Ads: hit a profiles read (well-defined Ads rate limits)
#   .call_tool_expecting_upstream_error() -> dict
#       SP: hit a tool with missing required field that reaches upstream
#       Ads: same shape, Ads-side
#   .try_trigger_rate_limit_or_skip() -> dict | None
#   .clear_active_identity()
#   .valid_set_context_args() -> dict
