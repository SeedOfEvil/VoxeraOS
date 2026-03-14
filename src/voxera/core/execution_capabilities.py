from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import SkillManifest
from ..policy import CAPABILITY_EFFECT_CLASS


class _StrEnum(str, Enum):
    pass


class SideEffectClass(_StrEnum):
    CLASS_A = "class_a"
    CLASS_B = "class_b"
    CLASS_C = "class_c"


class FilesystemScope(_StrEnum):
    NONE = "none"
    CONFINED = "confined"
    BROADER = "broader"


class NetworkScope(_StrEnum):
    NONE = "none"
    READ_ONLY = "read_only"
    BROADER = "broader"


class SandboxProfile(_StrEnum):
    HOST_LOCAL = "host_local"
    SANDBOX_NO_NETWORK = "sandbox_no_network"
    SANDBOX_NETWORK_SCOPED = "sandbox_network_scoped"


@dataclass(frozen=True)
class SecretRequirement:
    ref: str
    required: bool = True
    purpose: str | None = None


@dataclass(frozen=True)
class ExecutionCapabilityDeclaration:
    side_effect_class: SideEffectClass
    needs_network: bool
    network_scope: NetworkScope
    allowed_domains: tuple[str, ...] = field(default_factory=tuple)
    fs_scope: FilesystemScope = FilesystemScope.CONFINED
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    secret_refs: tuple[SecretRequirement, ...] = field(default_factory=tuple)
    sandbox_profile: SandboxProfile = SandboxProfile.HOST_LOCAL
    expected_artifacts: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "side_effect_class": self.side_effect_class.value,
            "needs_network": self.needs_network,
            "network_scope": self.network_scope.value,
            "allowed_domains": list(self.allowed_domains),
            "fs_scope": self.fs_scope.value,
            "allowed_paths": list(self.allowed_paths),
            "secret_refs": [
                {"ref": item.ref, "required": item.required, "purpose": item.purpose}
                for item in self.secret_refs
            ],
            "sandbox_profile": self.sandbox_profile.value,
            "expected_artifacts": list(self.expected_artifacts),
        }


def normalize_manifest_capabilities(manifest: SkillManifest) -> ExecutionCapabilityDeclaration:
    network_scope: NetworkScope = (
        NetworkScope.BROADER if manifest.needs_network else NetworkScope.NONE
    )

    fs_scope_map = {
        "workspace_only": FilesystemScope.CONFINED,
        "read_only": FilesystemScope.NONE,
        "broader": FilesystemScope.BROADER,
    }
    fs_scope = fs_scope_map[manifest.fs_scope]

    sandbox_profile: SandboxProfile = SandboxProfile.HOST_LOCAL
    if manifest.exec_mode == "sandbox":
        sandbox_profile = (
            SandboxProfile.SANDBOX_NETWORK_SCOPED
            if manifest.needs_network
            else SandboxProfile.SANDBOX_NO_NETWORK
        )

    effect_classes: set[str] = set()
    for cap in manifest.capabilities:
        effect_class = CAPABILITY_EFFECT_CLASS.get(cap)
        if effect_class is not None:
            effect_classes.add(effect_class)

    side_effect_class = _derive_side_effect_class(
        risk=manifest.risk,
        effect_classes=effect_classes,
        fs_scope=fs_scope,
        network_scope=network_scope,
    )

    return ExecutionCapabilityDeclaration(
        side_effect_class=side_effect_class,
        needs_network=network_scope != NetworkScope.NONE,
        network_scope=network_scope,
        fs_scope=fs_scope,
        allowed_paths=("$VOXERA_JOB_WORKSPACE",) if fs_scope == FilesystemScope.CONFINED else (),
        sandbox_profile=sandbox_profile,
        expected_artifacts=tuple(manifest.output_artifacts),
    )


def _derive_side_effect_class(
    *,
    risk: str,
    effect_classes: set[str],
    fs_scope: FilesystemScope,
    network_scope: NetworkScope,
) -> SideEffectClass:
    if risk == "high":
        return SideEffectClass.CLASS_C
    if fs_scope == FilesystemScope.BROADER or network_scope == NetworkScope.BROADER:
        return SideEffectClass.CLASS_C
    if "execute" in effect_classes or "write" in effect_classes:
        return SideEffectClass.CLASS_B
    if risk == "medium":
        return SideEffectClass.CLASS_B
    return SideEffectClass.CLASS_A
