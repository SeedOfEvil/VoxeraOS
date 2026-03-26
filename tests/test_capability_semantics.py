from voxera.core.capability_semantics import capability_semantic, manifest_capability_semantics
from voxera.models import SkillManifest


def _manifest(**overrides):
    payload = {
        "id": "demo.skill",
        "name": "Demo",
        "description": "Demo",
        "entrypoint": "voxera_builtin_skills.system_status:run",
        "capabilities": ["state.read"],
    }
    payload.update(overrides)
    return SkillManifest(**payload)


def test_capability_semantic_for_file_delete_is_destructive():
    semantic = capability_semantic("file.delete")
    assert semantic is not None
    assert semantic.effect_class == "write"
    assert semantic.intent_class == "destructive"
    assert semantic.policy_field == "file_delete"


def test_manifest_capability_semantics_projects_boundaries_and_intent():
    projection = manifest_capability_semantics(
        _manifest(capabilities=["files.write", "file.delete"], fs_scope="workspace_only")
    )

    assert projection["intent_class"] == "destructive"
    assert projection["resource_boundaries"] == {
        "filesystem": True,
        "network": False,
        "secrets": False,
        "system": False,
    }
    assert projection["approval_policy_fields"] == ["file_delete"]
