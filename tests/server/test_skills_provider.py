"""End-to-end smoke for the FastMCP skills provider wiring.

Like ``test_code_mode_integration.py``, this builds a real
:class:`fastmcp.FastMCP` via ``create_mcp_server`` and asserts on what an
MCP client would actually see when it calls ``list_resources()`` /
``read_resource()``. No stub providers — the value is verifying that
the bundled ``skills/openbridge-mcp/`` directory shows up on the wire
with the *right* set of resources.

The contract this file locks in:

* The repo-bundled skill at ``skills/openbridge-mcp/`` is loaded into
  the server. Connected clients see at least one ``skill://`` resource.
* The main ``SKILL.md`` is readable end-to-end.
* The skill's manifest (synthetic JSON resource emitted by FastMCP)
  references the supporting files we author (``references/workflows.md``,
  ``references/code-mode.md``). Validated by required-filename presence,
  not exact manifest structure, so a future FastMCP minor-version
  manifest tweak doesn't false-fail us.
* The supporting reference docs are reachable as resources. This case
  exists specifically because the wiring uses
  ``supporting_files="resources"`` — the FastMCP default
  (``"template"``) would NOT expose ``references/*.md`` and this test
  would fail with a clean signal.
* If the ``skills/`` directory is missing (stripped install), the
  server still boots cleanly with no skill resources.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.server import mcp_server as mcp_server_module
from src.server.mcp_server import create_mcp_server


SKILL_NAME = "openbridge-mcp"
SKILL_URI = f"skill://{SKILL_NAME}/SKILL.md"
MANIFEST_URI = f"skill://{SKILL_NAME}/_manifest"
WORKFLOWS_URI = f"skill://{SKILL_NAME}/references/workflows.md"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Pin auth off and Docket to memory so tests stay offline."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "memory://")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)


def _resource_text(result) -> str:
    """Pull the text payload off a ``read_resource()`` return value.

    FastMCP returns a ``ResourceResult`` wrapping one or more
    ``ResourceContent`` entries. Skills-provider text/JSON resources
    expose the body on ``.content`` (with ``mime_type`` set
    independently). Older or alternate FastMCP resource shapes use
    ``.text``. Try both, fall back to ``str(result)`` if neither.
    """
    contents = getattr(result, "contents", None)
    if contents:
        first = contents[0]
        for attr in ("content", "text"):
            value = getattr(first, attr, None)
            if value is not None:
                return value
    return str(result)


# ---------------------------------------------------------------------------
# Skill discovery surface
# ---------------------------------------------------------------------------


def test_skill_resources_listed():
    """At least one ``skill://openbridge-mcp/...`` resource must surface
    on the wire — the most basic 'is the provider registered?' check."""
    mcp = create_mcp_server()
    resources = asyncio.run(mcp.list_resources())
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith("skill://")]
    assert any(SKILL_NAME in uri for uri in skill_uris), (
        f"Expected at least one skill://openbridge-mcp/* URI in list_resources(); "
        f"got: {skill_uris!r}"
    )


def test_main_skill_md_readable():
    """The SKILL.md content reaches the wire intact."""
    mcp = create_mcp_server()
    result = asyncio.run(mcp.read_resource(SKILL_URI))
    text = _resource_text(result)
    # Shibboleth: the SKILL.md frontmatter description starts with this exact phrase.
    assert "Drive the Openbridge platform" in text, (
        f"SKILL.md content did not include the expected shibboleth. "
        f"Got first 200 chars: {text[:200]!r}"
    )


def test_skill_manifest_lists_required_files():
    """The synthetic ``_manifest`` resource must reference the supporting
    files we ship with the skill. Validates required filenames only —
    not exact JSON structure — so FastMCP minor-version manifest tweaks
    don't false-fail us."""
    mcp = create_mcp_server()
    result = asyncio.run(mcp.read_resource(MANIFEST_URI))
    raw = _resource_text(result)
    manifest = json.loads(raw)
    # Flatten the manifest to a string so we can match filenames
    # regardless of whether they live under "files", "resources", "paths",
    # or some other key in a future schema version.
    blob = json.dumps(manifest)
    for required in ("references/workflows.md", "references/code-mode.md"):
        assert required in blob, (
            f"Manifest does not reference {required!r}. "
            f"Manifest content: {blob!r}"
        )


def test_supporting_reference_doc_readable():
    """``references/workflows.md`` must be reachable as a resource.

    This case exercises the ``supporting_files='resources'`` kwarg
    specifically. With the FastMCP default (``'template'``) only
    ``SKILL.md`` and ``_manifest`` are exposed and this URI does not
    exist — ``read_resource()`` would raise.
    """
    mcp = create_mcp_server()
    result = asyncio.run(mcp.read_resource(WORKFLOWS_URI))
    text = _resource_text(result)
    # Shibboleth: the workflows.md preamble.
    assert "End-to-end recipes" in text, (
        f"workflows.md content did not match expected shibboleth. "
        f"Got first 200 chars: {text[:200]!r}"
    )


def test_provider_resilient_to_missing_dir(monkeypatch):
    """When the skills directory is absent (stripped install, image
    build that didn't COPY skills/), the server must still boot and
    serve tool requests — no skill resources surface, no exception."""
    monkeypatch.setattr(
        mcp_server_module,
        "_resolve_skills_root",
        lambda: None,
    )
    mcp = create_mcp_server()
    resources = asyncio.run(mcp.list_resources())
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith("skill://")]
    assert skill_uris == [], (
        f"Expected zero skill:// URIs when skills/ is missing; got: {skill_uris!r}"
    )
