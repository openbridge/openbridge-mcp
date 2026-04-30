"""Tests for ``src.server.code_mode``.

Contract (``docs/tool-contracts.md``):
- ``_env_bool`` treats absent as default; `0/false/no/off` (case-insensitive,
  stripped) as False; everything else as True.
- ``is_code_mode_enabled`` defaults to True when CODE_MODE is unset.
- ``create_code_mode_transform`` fails fast (raises ValueError) on
  non-numeric ``CODE_MODE_MAX_DURATION_SECS`` / ``CODE_MODE_MAX_MEMORY``.
  This is intentional: config errors at startup beat surprise behavior at
  tool execution time.
"""

import pytest

from src.server import code_mode


# ---------------------------------------------------------------------------
# _env_bool — falsy set is closed, everything else is True.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", False),
        ("false", False),
        ("False", False),  # case-insensitive
        ("FALSE", False),
        ("no", False),
        ("off", False),
        (" off ", False),  # whitespace-stripped
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("", True),  # empty string is NOT in the falsy set per current contract
        ("anything-else", True),
    ],
)
def test_env_bool_known_values(monkeypatch, value, expected):
    monkeypatch.setenv("SOMEVAR", value)
    assert code_mode._env_bool("SOMEVAR", default=True) is expected
    # Default should not matter when the variable is set.
    assert code_mode._env_bool("SOMEVAR", default=False) is expected


def test_env_bool_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOMEVAR", raising=False)
    assert code_mode._env_bool("SOMEVAR", default=True) is True
    assert code_mode._env_bool("SOMEVAR", default=False) is False


# ---------------------------------------------------------------------------
# is_code_mode_enabled
# ---------------------------------------------------------------------------


def test_is_code_mode_enabled_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("CODE_MODE", raising=False)
    assert code_mode.is_code_mode_enabled() is True


def test_is_code_mode_enabled_respects_false_value(monkeypatch):
    monkeypatch.setenv("CODE_MODE", "false")
    assert code_mode.is_code_mode_enabled() is False


# ---------------------------------------------------------------------------
# create_code_mode_transform — fail-fast on bad numeric env.
#
# These tests stub the fastmcp experimental import so the module doesn't
# need the optional extra installed to run.
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_fastmcp_transforms(monkeypatch):
    """Provide a fake ``fastmcp.experimental.transforms.code_mode`` module
    so create_code_mode_transform can import without the extras package.

    Uses ``monkeypatch.setitem`` for sys.modules so the original module
    (now installed via the production runtime extras) is restored at
    teardown. Without this, the stubbed ``_Fake`` class would leak into
    later tests that build a real CodeMode transform — those tests
    would then crash with ``'_Fake' object has no attribute 'list_tools'``.
    """
    import sys
    import types

    stub = types.ModuleType("fastmcp.experimental.transforms.code_mode")

    class _Fake:
        def __init__(self, *a, **kw):
            pass

    stub.CodeMode = _Fake
    stub.GetSchemas = _Fake
    stub.GetTags = _Fake
    stub.MontySandboxProvider = _Fake
    stub.Search = _Fake

    # Use monkeypatch.setitem so the previous module (real one if loaded,
    # else absent) is restored at teardown. Walking parent modules is
    # only needed if the real module isn't present — defensive for older
    # FastMCP installs.
    for name in (
        "fastmcp",
        "fastmcp.experimental",
        "fastmcp.experimental.transforms",
    ):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(
        sys.modules, "fastmcp.experimental.transforms.code_mode", stub,
    )


def test_create_code_mode_transform_happy_path(_stub_fastmcp_transforms, monkeypatch):
    monkeypatch.delenv("CODE_MODE_INCLUDE_TAGS", raising=False)
    monkeypatch.delenv("CODE_MODE_MAX_DURATION_SECS", raising=False)
    monkeypatch.delenv("CODE_MODE_MAX_MEMORY", raising=False)
    # Should not raise.
    result = code_mode.create_code_mode_transform()
    assert result is not None


def test_create_code_mode_transform_fails_fast_on_non_numeric_duration(
    _stub_fastmcp_transforms, monkeypatch
):
    """Config error at startup beats surprise behavior at runtime."""
    monkeypatch.setenv("CODE_MODE_MAX_DURATION_SECS", "not-a-number")
    with pytest.raises(ValueError):
        code_mode.create_code_mode_transform()


def test_create_code_mode_transform_fails_fast_on_non_numeric_memory(
    _stub_fastmcp_transforms, monkeypatch
):
    monkeypatch.setenv("CODE_MODE_MAX_MEMORY", "not-a-number")
    with pytest.raises(ValueError):
        code_mode.create_code_mode_transform()


def test_create_code_mode_transform_raises_import_error_without_extras(monkeypatch):
    """If the optional extras are not importable, surface ImportError with
    the install instruction — do not silently fall back."""
    import sys

    # Remove any previously-stubbed module so the real import fails.
    for name in list(sys.modules):
        if name.startswith("fastmcp.experimental"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    # Make the import unambiguously fail.
    class _ImportBlocker:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith("fastmcp.experimental.transforms.code_mode"):
                raise ImportError(f"blocked for test: {fullname}")
            return None

    sys.meta_path.insert(0, _ImportBlocker())
    try:
        with pytest.raises(ImportError) as exc_info:
            code_mode.create_code_mode_transform()
        assert "fastmcp[code-mode]" in str(exc_info.value)
    finally:
        sys.meta_path.remove(sys.meta_path[0])
