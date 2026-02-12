from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable, Dict, Optional
import yaml
from ..models import SkillManifest

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"

class SkillRegistry:
    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        self._cache: Dict[str, SkillManifest] = {}

    def discover(self) -> Dict[str, SkillManifest]:
        manifests = {}
        if not self.skills_dir.exists():
            return manifests
        for yml in self.skills_dir.rglob("manifest.yml"):
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            m = SkillManifest.model_validate(data)
            manifests[m.id] = m
        self._cache = manifests
        return manifests

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
