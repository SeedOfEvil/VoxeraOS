.PHONY: install dev panel test lint fmt

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
