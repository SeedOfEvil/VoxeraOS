# Contributing to VoxeraOS

VoxeraOS welcomes contributions, bug reports, feedback, and experimentation.

This is a one-person evenings-and-weekends project in alpha. Contributions of all sizes are appreciated — from bug reports and documentation fixes to feature ideas and code contributions.

## Getting started

```bash
git clone https://github.com/SeedOfEvil/VoxeraOS.git
cd VoxeraOS
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make dev
```

## Before opening a PR

Run the canonical merge gate:

```bash
make merge-readiness-check
```

This runs format checks, linting, type checks, release-consistency checks, and the security red-team regression suite.

For broader confidence:

```bash
make validation-check     # quick gate (format/lint/type + golden + security + contract suites)
make full-validation-check  # full suite including all tests and E2E
```

## Code style

- Code is formatted and linted with [Ruff](https://docs.astral.sh/ruff/)
- Type checking with [mypy](https://mypy-lang.org/)
- Line length: 100 characters
- Target Python: 3.10+

## Architecture guidelines

When extending VoxeraOS:

- Keep composition roots thin (`queue_daemon.py`, `panel/app.py`, `cli.py`)
- Add new behavior in focused domain modules (`queue_*`, `routes_*`, `cli_*`)
- Preserve operator-visible contracts (queue paths, CLI flags, panel route behavior) unless intentionally versioned
- Prefer additive, auditable workflows over implicit behavior
- Fail closed when uncertain — no degraded-but-executing mode

## Testing

- Golden contract tests validate operator-visible CLI surfaces (`make golden-check`)
- Red-team security tests validate adversarial fail-closed guardrails (`make security-check`)
- Contract snapshot tests validate queue/CLI/doctor behavioral contracts
- When changing CLI output, update golden baselines intentionally with `make golden-update`

## Reporting bugs

[Open a GitHub issue](https://github.com/SeedOfEvil/VoxeraOS/issues) with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Output from `voxera doctor --quick` if relevant

## Security issues

For security vulnerabilities, see [SECURITY.md](SECURITY.md).

## Provider testing

OpenRouter is the only officially tested provider path today. If you test VoxeraOS with other models or providers (other OpenRouter models, Ollama, etc.), please share your results via a GitHub issue — this helps everyone.

## What to expect

This is an alpha project. APIs, CLI surfaces, and internal contracts may change between releases. The project values honest communication over stability promises — breaking changes will be documented in the changelog and release notes.

## License

By contributing to VoxeraOS, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
