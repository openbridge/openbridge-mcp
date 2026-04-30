"""Shared fixtures for the cross-server envelope conformance suite.

The conformance suite validates that envelope JSON emitted by any conforming
MCP server matches the v1 contract defined in :file:`/CONTRACT.md` and the
JSON Schema at :file:`/schemas/error-envelope.schema.json`.

Both ``amazon_sp_mcp`` and ``amazon_ads_mcp`` are expected to validate their
own envelope outputs against this suite. Each server may run a subset of
fixtures relevant to its taxonomy (e.g., SP runs ``sp_*`` fixtures; Ads runs
``ads_*`` fixtures once Phase 2 ships).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFORMANCE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = CONFORMANCE_DIR / "fixtures"
SCHEMA_PATH = CONFORMANCE_DIR.parent.parent / "schemas" / "error-envelope.schema.json"


@pytest.fixture(scope="session")
def envelope_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def all_fixtures() -> list[tuple[str, dict]]:
    fixtures: list[tuple[str, dict]] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        fixtures.append((path.name, data))
    return fixtures


@pytest.fixture(scope="session")
def envelope_fixtures(all_fixtures) -> list[tuple[str, dict]]:
    """Fixtures that carry an ``envelope`` key (error responses)."""
    return [(name, data["envelope"]) for name, data in all_fixtures if "envelope" in data]


@pytest.fixture(scope="session")
def response_fixtures(all_fixtures) -> list[tuple[str, dict]]:
    """Fixtures that carry a ``response`` key (success responses with `_meta`)."""
    return [(name, data["response"]) for name, data in all_fixtures if "response" in data]
