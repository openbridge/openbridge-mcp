from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def test_release_workflow_uses_least_privilege_and_trusted_publishing():
    workflow_text = WORKFLOW.read_text()
    assert "pull-requests: write" not in workflow_text
    assert "issues: write" not in workflow_text
    assert "pip install" not in workflow_text
    assert "PYPI_API_TOKEN" not in workflow_text
    assert "continue-on-error: true" not in workflow_text
    assert "id-token: write" in workflow_text
    assert "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33" in workflow_text


def test_release_actions_are_immutable_sha_pins():
    workflow_text = WORKFLOW.read_text()
    assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in workflow_text
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow_text
    assert "softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65" in workflow_text
