from __future__ import annotations

from pathlib import Path

_SHARED_PROMPT_DOCS: tuple[str, ...] = (
    "00-system-overview.md",
    "01-platform-boundaries.md",
    "02-role-map.md",
    "03-runtime-technical-overview.md",
)

_ROLE_DOCS: dict[str, str] = {
    "vera": "roles/vera.md",
    "hidden_compiler": "roles/hidden-compiler.md",
    "planner": "roles/planner.md",
    "verifier": "roles/verifier.md",
    "web_investigator": "roles/web-investigator.md",
}

_ROLE_CAPABILITY_DOCS: dict[str, tuple[str, ...]] = {
    "vera": (
        "capabilities/handoff-and-submit-rules.md",
        "capabilities/queue-object-model.md",
        "capabilities/web-investigation-rules.md",
    ),
    "hidden_compiler": (
        "capabilities/preview-payload-schema.md",
        "capabilities/handoff-and-submit-rules.md",
        "capabilities/queue-object-model.md",
        "capabilities/queue-lifecycle.md",
        "capabilities/artifacts-and-evidence.md",
        "capabilities/hidden-compiler-payload-guidance.md",
    ),
    "planner": (
        "capabilities/queue-object-model.md",
        "capabilities/queue-lifecycle.md",
        "capabilities/artifacts-and-evidence.md",
    ),
    "verifier": (
        "capabilities/queue-object-model.md",
        "capabilities/artifacts-and-evidence.md",
        "capabilities/queue-lifecycle.md",
    ),
    "web_investigator": ("capabilities/web-investigation-rules.md",),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _prompt_docs_root(*, docs_root: Path | None = None) -> Path:
    return docs_root or (_repo_root() / "docs" / "prompts")


def _resolve_prompt_path(relative_path: str, *, docs_root: Path | None = None) -> Path:
    root = _prompt_docs_root(docs_root=docs_root)
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise ValueError(f"Prompt doc path escapes docs/prompts: {relative_path}")
    return candidate


def load_prompt_doc(relative_path: str, *, docs_root: Path | None = None) -> str:
    path = _resolve_prompt_path(relative_path, docs_root=docs_root)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Prompt doc not found: {relative_path}") from exc


def compose_prompt_docs(*relative_paths: str, docs_root: Path | None = None) -> str:
    sections = [load_prompt_doc(path, docs_root=docs_root) for path in relative_paths]
    return "\n\n".join(section for section in sections if section)


def compose_prompt(role: str, capabilities: list[str] | None = None) -> str:
    if role not in _ROLE_DOCS:
        raise ValueError(f"Unknown prompt role: {role}")
    capability_paths = (
        tuple(capabilities) if capabilities is not None else _ROLE_CAPABILITY_DOCS[role]
    )
    ordered_paths = (*_SHARED_PROMPT_DOCS, _ROLE_DOCS[role], *capability_paths)
    return compose_prompt_docs(*ordered_paths)


def compose_vera_prompt() -> str:
    return compose_prompt("vera")


def compose_hidden_compiler_prompt() -> str:
    return compose_prompt("hidden_compiler")


def compose_planner_prompt() -> str:
    return compose_prompt("planner")


def compose_verifier_prompt() -> str:
    return compose_prompt("verifier")


def compose_web_investigator_prompt() -> str:
    return compose_prompt("web_investigator")
