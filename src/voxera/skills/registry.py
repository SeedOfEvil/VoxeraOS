from __future__ import annotations

import importlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from ..models import SkillManifest
from ..policy import CAPABILITY_EFFECT_CLASS

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"

SkillHealthStatus = Literal["valid", "invalid", "incomplete", "warning"]


class SkillHealthIssue:
    def __init__(
        self,
        *,
        skill_id: str,
        manifest_path: Path,
        status: SkillHealthStatus,
        reason_code: str,
        message: str,
        hint: str,
    ) -> None:
        self.skill_id = skill_id
        self.manifest_path = manifest_path
        self.status = status
        self.reason_code = reason_code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "manifest_path": str(self.manifest_path),
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "hint": self.hint,
        }


class SkillDiscoveryReport:
    def __init__(
        self,
        *,
        valid: dict[str, SkillManifest],
        issues: list[SkillHealthIssue],
        discovered_paths: list[Path],
    ) -> None:
        self.valid = valid
        self.issues = issues
        self.discovered_paths = discovered_paths

    @property
    def counts(self) -> dict[str, int]:
        counter = Counter(issue.status for issue in self.issues)
        return {
            "valid": len(self.valid),
            "invalid": counter.get("invalid", 0),
            "incomplete": counter.get("incomplete", 0),
            "warning": counter.get("warning", 0),
            "total": len(self.discovered_paths),
        }

    @property
    def blocks_runtime(self) -> bool:
        return any(issue.status in {"invalid", "incomplete"} for issue in self.issues)


class SkillRegistry:
    def __init__(self, skills_dir: Path | None = None):
        self.skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        self._cache: dict[str, SkillManifest] = {}
        self._last_report = SkillDiscoveryReport(valid={}, issues=[], discovered_paths=[])

    def discover(self) -> dict[str, SkillManifest]:
        report = self.discover_with_report()
        invalid = [issue for issue in report.issues if issue.status == "invalid"]
        if invalid:
            first = invalid[0]
            raise ValueError(
                f"Invalid skill manifest '{first.skill_id}' at {first.manifest_path}: {first.message}"
            )
        self._cache = report.valid
        return report.valid

    def discover_with_report(self) -> SkillDiscoveryReport:
        manifests: dict[str, SkillManifest] = {}
        issues: list[SkillHealthIssue] = []
        discovered_paths: list[Path] = []

        if not self.skills_dir.exists():
            report = SkillDiscoveryReport(valid=manifests, issues=issues, discovered_paths=[])
            self._last_report = report
            return report

        for yml in sorted(self.skills_dir.rglob("manifest.yml")):
            discovered_paths.append(yml)
            loaded = yaml.safe_load(yml.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                issues.append(
                    SkillHealthIssue(
                        skill_id=f"<{yml.parent.name}>",
                        manifest_path=yml,
                        status="invalid",
                        reason_code="malformed_schema",
                        message="manifest root must be a mapping/object",
                        hint="fix_manifest",
                    )
                )
                continue

            skill_id = str(loaded.get("id") or f"<{yml.parent.name}>")
            try:
                manifest = SkillManifest.model_validate(loaded)
            except ValidationError as exc:
                errors = []
                for err in exc.errors():
                    path = ".".join(str(piece) for piece in err.get("loc", [])) or "manifest"
                    errors.append(f"{path}: {err.get('msg', 'invalid value')}")
                issues.append(
                    SkillHealthIssue(
                        skill_id=skill_id,
                        manifest_path=yml,
                        status="invalid",
                        reason_code="malformed_schema",
                        message="; ".join(errors),
                        hint="fix_manifest",
                    )
                )
                continue

            manifest_issues = _classify_manifest_health(manifest=manifest, manifest_path=yml)
            if any(issue.status in {"invalid", "incomplete"} for issue in manifest_issues):
                issues.extend(manifest_issues)
                continue

            manifests[manifest.id] = manifest
            issues.extend(manifest_issues)

        report = SkillDiscoveryReport(
            valid=manifests,
            issues=sorted(
                issues,
                key=lambda item: (
                    item.status,
                    item.skill_id,
                    item.reason_code,
                    item.message,
                ),
            ),
            discovered_paths=discovered_paths,
        )
        self._last_report = report
        return report

    @property
    def last_report(self) -> SkillDiscoveryReport:
        return self._last_report

    def get(self, skill_id: str) -> SkillManifest:
        if not self._cache:
            self.discover()
        if skill_id not in self._cache:
            raise KeyError(f"Unknown skill: {skill_id}")
        return self._cache[skill_id]

    def load_entrypoint(self, manifest: SkillManifest) -> Callable:
        mod_name, func_name = manifest.entrypoint.split(":", 1)
        mod = importlib.import_module(mod_name)
        return getattr(mod, func_name)


def _classify_manifest_health(
    *, manifest: SkillManifest, manifest_path: Path
) -> list[SkillHealthIssue]:
    issues: list[SkillHealthIssue] = []
    if len(manifest.capabilities) == 0:
        issues.append(
            SkillHealthIssue(
                skill_id=manifest.id,
                manifest_path=manifest_path,
                status="incomplete",
                reason_code="missing_capability_metadata",
                message="capabilities must declare at least one runtime capability",
                hint="add_capabilities",
            )
        )
    else:
        unknown = sorted(cap for cap in manifest.capabilities if cap not in CAPABILITY_EFFECT_CLASS)
        if unknown:
            issues.append(
                SkillHealthIssue(
                    skill_id=manifest.id,
                    manifest_path=manifest_path,
                    status="invalid",
                    reason_code="unknown_capability_metadata",
                    message=f"unknown capabilities: {', '.join(unknown)}",
                    hint="fix_manifest",
                )
            )

    has_blocking_issue = any(issue.status in {"invalid", "incomplete"} for issue in issues)
    if not has_blocking_issue and not manifest.output_schema:
        issues.append(
            SkillHealthIssue(
                skill_id=manifest.id,
                manifest_path=manifest_path,
                status="warning",
                reason_code="missing_output_schema",
                message="output_schema is not declared",
                hint="recommend_add_output_schema",
            )
        )

    if manifest.exec_mode == "sandbox" and manifest.fs_scope == "broader":
        issues.append(
            SkillHealthIssue(
                skill_id=manifest.id,
                manifest_path=manifest_path,
                status="invalid",
                reason_code="sandbox_fs_scope_invalid",
                message="sandbox skills cannot declare fs_scope=broader",
                hint="fix_manifest",
            )
        )

    return issues
