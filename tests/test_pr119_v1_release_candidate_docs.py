from pathlib import Path

DOC = Path("docs/V1_RELEASE_CANDIDATE.md")


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_doc_exists() -> None:
    assert DOC.exists()


def test_required_links_exist() -> None:
    targets = [
        "README.md",
        "OPS.md",
        "docs/v1-scope.md",
        "docs/V1_COMMAND_SURFACE.md",
        "docs/safety.md",
    ]
    for target in targets:
        assert "docs/V1_RELEASE_CANDIDATE.md" in _read(target)


def test_required_sections_present() -> None:
    text = _read(str(DOC))
    sections = [
        "V1 promise",
        "What V1 includes",
        "What V1 does not include",
        "Required local/dev validation",
        "Required Docker01 smoke validation",
        "Required deterministic ask validation",
        "Required artifact validation",
        "Safety invariants",
        "Known acceptable caveats",
        "Hard blockers",
        "Docker01 handoff template",
        "Final V1 release sign-off",
    ]
    for section in sections:
        assert section in text


def test_required_validation_commands_present() -> None:
    text = _read(str(DOC))
    for cmd in [
        "./scripts/v1_validate.sh --quick",
        "./scripts/v1_validate.sh --full",
        "./scripts/v1_validate.sh --quick --packet",
        "./scripts/v1_validate.sh --quick --export-packet",
        "pytest -q",
        "ruff check .",
        "python -m compileall -q src tests",
    ]:
        assert cmd in text


def test_required_runtime_commands_present() -> None:
    text = _read(str(DOC))
    for cmd in [
        "shellforgeai version",
        "shellforgeai doctor",
        "shellforgeai model doctor",
        "shellforgeai v1 check --profile quick --json",
        "shellforgeai ops report --json",
        "shellforgeai remediation self-test --profile full --json",
    ]:
        assert cmd in text


def test_artifact_commands_present() -> None:
    text = _read(str(DOC))
    for cmd in [
        "shellforgeai ops report --save --json",
        "shellforgeai ops report validate",
        "shellforgeai ops report export",
        "shellforgeai ops report export-validate",
        "shellforgeai v1 packet --save --json",
        "shellforgeai v1 packet validate",
        "shellforgeai v1 packet export",
        "shellforgeai v1 packet export-validate",
    ]:
        assert cmd in text


def test_mutation_refusal_and_non_goals_present() -> None:
    text = _read(str(DOC))
    required = [
        'shellforgeai ask "please restart shellforgeai"',
        "production autonomous remediation",
        "natural-language mutation execution",
        "Docker/Compose mutation",
        "arbitrary shell execution",
    ]
    for needle in required:
        assert needle in text


def test_dangerous_commands_not_listed_as_release_steps() -> None:
    text = _read(str(DOC)).lower()
    dangerous = [
        "docker restart",
        "docker compose restart",
        "docker compose up",
        "docker compose down",
        "docker system prune",
        "docker volume prune",
        "cleanup execute --confirm",
        "remediation execute --confirm",
        "rollback-execute --confirm",
    ]
    for cmd in dangerous:
        if cmd in text:
            assert any(
                ctx in text
                for ctx in ["does not include", "hard blocker", "refus", "governed gate"]
            )
