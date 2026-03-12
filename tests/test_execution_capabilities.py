from voxera.core.execution_capabilities import (
    FilesystemScope,
    NetworkScope,
    SandboxProfile,
    SideEffectClass,
    normalize_manifest_capabilities,
)
from voxera.models import SkillManifest


def _manifest(**overrides):
    payload = {
        "id": "test.skill",
        "name": "Test Skill",
        "description": "Test",
        "entrypoint": "voxera.skills.demo:run",
        "capabilities": ["files.read"],
    }
    payload.update(overrides)
    return SkillManifest(**payload)


def test_normalize_manifest_capabilities_read_only_defaults():
    declaration = normalize_manifest_capabilities(_manifest())

    assert declaration.side_effect_class == SideEffectClass.CLASS_A
    assert declaration.network_scope == NetworkScope.NONE
    assert declaration.fs_scope == FilesystemScope.CONFINED
    assert declaration.sandbox_profile == SandboxProfile.HOST_LOCAL
    assert declaration.expected_artifacts == ()


def test_normalize_manifest_capabilities_high_risk_broader_network_and_sandbox():
    declaration = normalize_manifest_capabilities(
        _manifest(
            capabilities=["sandbox.exec"],
            risk="high",
            needs_network=True,
            fs_scope="broader",
            exec_mode="sandbox",
            output_artifacts=["execution_result", "step_results"],
        )
    )

    assert declaration.side_effect_class == SideEffectClass.CLASS_C
    assert declaration.network_scope == NetworkScope.BROADER
    assert declaration.fs_scope == FilesystemScope.BROADER
    assert declaration.sandbox_profile == SandboxProfile.SANDBOX_NETWORK_SCOPED
    assert declaration.expected_artifacts == ("execution_result", "step_results")
