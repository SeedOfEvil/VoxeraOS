from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models import SkillManifest

CapabilityEffectClass = Literal["read", "write", "execute"]
CapabilityIntentClass = Literal["read_only", "mutating", "destructive"]
ResourceBoundary = Literal["filesystem", "network", "secrets", "system"]


@dataclass(frozen=True)
class CapabilitySemantic:
    capability: str
    effect_class: CapabilityEffectClass
    intent_class: CapabilityIntentClass
    policy_field: str | None
    resource_boundaries: tuple[ResourceBoundary, ...]
    operator_summary: str


CAPABILITY_SEMANTICS: dict[str, CapabilitySemantic] = {
    "apps.open": CapabilitySemantic(
        capability="apps.open",
        effect_class="execute",
        intent_class="mutating",
        policy_field="open_apps",
        resource_boundaries=("system",),
        operator_summary="Launches local applications.",
    ),
    "network.change": CapabilitySemantic(
        capability="network.change",
        effect_class="write",
        intent_class="mutating",
        policy_field="network_changes",
        resource_boundaries=("network",),
        operator_summary="Performs network side effects (connections or remote writes).",
    ),
    "install.packages": CapabilitySemantic(
        capability="install.packages",
        effect_class="write",
        intent_class="mutating",
        policy_field="installs",
        resource_boundaries=("system", "network"),
        operator_summary="Installs software on the host.",
    ),
    "file.delete": CapabilitySemantic(
        capability="file.delete",
        effect_class="write",
        intent_class="destructive",
        policy_field="file_delete",
        resource_boundaries=("filesystem",),
        operator_summary="Deletes filesystem data.",
    ),
    "system.settings": CapabilitySemantic(
        capability="system.settings",
        effect_class="write",
        intent_class="mutating",
        policy_field="system_settings",
        resource_boundaries=("system",),
        operator_summary="Mutates local system settings.",
    ),
    "state.read": CapabilitySemantic(
        capability="state.read",
        effect_class="read",
        intent_class="read_only",
        policy_field=None,
        resource_boundaries=("system",),
        operator_summary="Reads local system state only.",
    ),
    "files.read": CapabilitySemantic(
        capability="files.read",
        effect_class="read",
        intent_class="read_only",
        policy_field=None,
        resource_boundaries=("filesystem",),
        operator_summary="Reads files only.",
    ),
    "files.write": CapabilitySemantic(
        capability="files.write",
        effect_class="write",
        intent_class="mutating",
        policy_field=None,
        resource_boundaries=("filesystem",),
        operator_summary="Creates or modifies files.",
    ),
    "clipboard.read": CapabilitySemantic(
        capability="clipboard.read",
        effect_class="read",
        intent_class="read_only",
        policy_field=None,
        resource_boundaries=("system",),
        operator_summary="Reads clipboard contents.",
    ),
    "clipboard.write": CapabilitySemantic(
        capability="clipboard.write",
        effect_class="write",
        intent_class="mutating",
        policy_field=None,
        resource_boundaries=("system",),
        operator_summary="Writes clipboard contents.",
    ),
    "window.read": CapabilitySemantic(
        capability="window.read",
        effect_class="read",
        intent_class="read_only",
        policy_field=None,
        resource_boundaries=("system",),
        operator_summary="Reads window/session metadata.",
    ),
    "sandbox.exec": CapabilitySemantic(
        capability="sandbox.exec",
        effect_class="execute",
        intent_class="mutating",
        policy_field=None,
        resource_boundaries=("system", "filesystem"),
        operator_summary="Executes commands in sandboxed runtime.",
    ),
}


CAPABILITY_EFFECT_CLASS: dict[str, CapabilityEffectClass] = {
    key: semantic.effect_class for key, semantic in CAPABILITY_SEMANTICS.items()
}


def capability_semantic(capability: str) -> CapabilitySemantic | None:
    return CAPABILITY_SEMANTICS.get(capability)


def manifest_capability_semantics(manifest: SkillManifest) -> dict[str, object]:
    """Centralized, inspectable semantics projection for a skill manifest."""
    known = [capability_semantic(cap) for cap in manifest.capabilities]
    present = [item for item in known if item is not None]

    boundaries = {
        "filesystem": any("filesystem" in item.resource_boundaries for item in present),
        "network": any("network" in item.resource_boundaries for item in present)
        or bool(manifest.needs_network),
        "secrets": any("secrets" in item.resource_boundaries for item in present),
        "system": any("system" in item.resource_boundaries for item in present),
    }

    intent_rank = {"read_only": 0, "mutating": 1, "destructive": 2}
    intent_class: CapabilityIntentClass = "read_only"
    for item in present:
        if intent_rank[item.intent_class] > intent_rank[intent_class]:
            intent_class = item.intent_class

    # Filesystem broader scope is operationally destructive-risking even without delete capability.
    if manifest.fs_scope == "broader" and intent_class == "read_only":
        intent_class = "mutating"

    return {
        "intent_class": intent_class,
        "effect_classes": sorted({item.effect_class for item in present}),
        "resource_boundaries": boundaries,
        "approval_policy_fields": sorted(
            {
                item.policy_field
                for item in present
                if item.policy_field is not None and str(item.policy_field).strip()
            }
        ),
        "declared_capabilities": sorted(manifest.capabilities),
    }
