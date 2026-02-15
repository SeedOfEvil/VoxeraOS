.PHONY: install dev panel test lint fmt e2e

install:
	python -m pip install -U pip
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install || true

panel:
	voxera panel

test:
	pytest

lint:
	ruff check .
	mypy src/voxera --ignore-missing-imports

fmt:
	ruff format .


e2e:
	bash scripts/e2e_smoke.sh
