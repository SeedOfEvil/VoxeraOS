.PHONY: venv install dev fmt fmt-check lint type test e2e check panel vera daemon-restart \
	 type-check type-check-strict update-mypy-baseline quality-check release-check merge-readiness-check \
	 golden-update golden-check security-check validation-check full-validation-check test-failed-sidecar update update-fast services-install services-restart services-status services-stop services-disable vera-start vera-stop vera-restart vera-status vera-logs premerge

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
SYSTEMD_SRC_DIR := deploy/systemd/user
VOXERA_UNITS := voxera-daemon.service voxera-panel.service voxera-vera.service
VERA_SERVICE := voxera-vera.service
VERA_HOST ?= 127.0.0.1
VERA_PORT ?= 8790
VOXERA_PROJECT_DIR := $(abspath .)
VENV_BIN := .venv/bin
PYTHON := $(VENV_BIN)/python
VENV_PY := $(PYTHON)
PIP := $(VENV_BIN)/pip
RUFF := $(VENV_BIN)/ruff
MYPY := $(VENV_BIN)/mypy
PYTEST := $(VENV_BIN)/pytest
VOXERA := $(VENV_BIN)/voxera
DEV_MARKER := .venv/.dev_installed
TEST_ENV_PREFIX := env -u VOXERA_OPS_BUNDLE_DIR -u VOXERA_QUEUE_LOCK_STALE_S -u VOXERA_QUEUE_FAILED_MAX_AGE_S -u VOXERA_QUEUE_FAILED_MAX_COUNT -u VOXERA_ARTIFACTS_RETENTION_DAYS -u VOXERA_ARTIFACTS_RETENTION_MAX_COUNT -u VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS -u VOXERA_QUEUE_PRUNE_MAX_COUNT -u VOXERA_PANEL_HOST -u VOXERA_PANEL_PORT -u VOXERA_PANEL_OPERATOR_USER -u VOXERA_PANEL_OPERATOR_PASSWORD -u VOXERA_PANEL_ENABLE_GET_MUTATIONS -u VOXERA_PANEL_CSRF_ENABLED -u VOXERA_DEV_MODE -u VOXERA_NOTIFY VOXERA_LOAD_DOTENV=0

$(VENV_PY):
	@if [ ! -x "$(VENV_PY)" ]; then python3 -m venv .venv; fi
	$(VENV_PY) -m pip install --upgrade pip

venv: $(VENV_PY)

install: venv
	$(PIP) install -e .

$(DEV_MARKER): pyproject.toml $(VENV_PY)
	$(PIP) install -e ".[dev]"
	touch $(DEV_MARKER)

dev: $(DEV_MARKER)
	-$(VENV_BIN)/pre-commit install --hook-type pre-commit --hook-type pre-push

fmt: $(DEV_MARKER)
	$(RUFF) format .

fmt-check: $(DEV_MARKER)
	$(RUFF) format --check .

lint: $(DEV_MARKER)
	$(RUFF) check .

type: $(DEV_MARKER)
	$(MYPY) src/voxera

test: $(DEV_MARKER)
	$(TEST_ENV_PREFIX) $(PYTEST) -q

e2e: $(DEV_MARKER)
	bash scripts/e2e_opsconsole.sh

check: fmt-check lint type test
ifeq ($(CHECK_E2E),1)
	$(MAKE) e2e
endif

panel: $(DEV_MARKER)
	$(VOXERA) panel --host 127.0.0.1 --port 8787

vera: $(DEV_MARKER)
	$(PYTHON) -m uvicorn voxera.vera_web.app:app --host $(VERA_HOST) --port $(VERA_PORT)

daemon-restart:
	systemctl --user daemon-reload
	systemctl --user restart voxera-daemon.service
	systemctl --user --no-pager status voxera-daemon.service | head -n 20

# Backward-compatible aliases
quality-check: fmt-check lint type

type-check: $(DEV_MARKER)
	$(PYTHON) scripts/mypy_ratchet.py

# Strict typing target for full-baseline cleanup workstreams.
type-check-strict: type

update-mypy-baseline: $(DEV_MARKER)
	$(PYTHON) scripts/mypy_ratchet.py --write-baseline

test-failed-sidecar: $(DEV_MARKER)
	$(TEST_ENV_PREFIX) $(PYTEST) -q tests/test_queue_daemon.py -k "failed_sidecar_schema_version_policy_rejects_unknown_future_version or queue_failure_lifecycle_smoke_sidecar_snapshot_then_prune"

release-check: $(DEV_MARKER)
	$(TEST_ENV_PREFIX) $(PYTEST) -q \
		tests/test_version_source.py \
		tests/test_panel.py::test_panel_app_uses_shared_version_source \
		tests/test_docs_consistency.py \
		tests/test_cli_version.py::test_root_version_option_prints_and_exits

merge-readiness-check: quality-check release-check security-check

golden-update: $(DEV_MARKER)
	$(PYTHON) tools/golden_surfaces.py --update

golden-check: $(DEV_MARKER)
	$(PYTHON) tools/golden_surfaces.py --check

security-check: $(DEV_MARKER)
	$(TEST_ENV_PREFIX) $(PYTEST) -q tests/test_security_redteam.py

validation-check: $(DEV_MARKER)
	$(RUFF) format --check .
	$(RUFF) check .
	$(MYPY) src/voxera
	$(MAKE) golden-check
	$(MAKE) security-check
	$(TEST_ENV_PREFIX) $(PYTEST) -q \
		tests/test_queue_daemon.py \
		tests/test_queue_daemon_contract_snapshot.py \
		tests/test_cli_contract_snapshot.py \
		tests/test_cli_queue.py \
		tests/test_doctor.py

full-validation-check: validation-check merge-readiness-check test-failed-sidecar
	$(TEST_ENV_PREFIX) $(PYTEST) -q
	bash scripts/e2e_golden4.sh

update: venv
	bash scripts/update.sh --smoke

update-fast: venv
	bash scripts/update.sh --skip-tests

services-install:
	mkdir -p "$(SYSTEMD_USER_DIR)"
	for unit in $(VOXERA_UNITS); do \
		sed "s|@VOXERA_PROJECT_DIR@|$(VOXERA_PROJECT_DIR)|g" "$(SYSTEMD_SRC_DIR)/$$unit" > "$(SYSTEMD_USER_DIR)/$$unit"; \
	done
	systemctl --user daemon-reload
	systemctl --user enable --now $(VOXERA_UNITS)

services-restart:
	systemctl --user daemon-reload
	for unit in $(VOXERA_UNITS); do \
		if systemctl --user is-enabled "$$unit" >/dev/null 2>&1; then \
			systemctl --user restart "$$unit"; \
		else \
			echo "Skipping $$unit (not enabled)."; \
		fi; \
	done

services-status:
	systemctl --user --no-pager status $(VOXERA_UNITS)

services-stop:
	systemctl --user stop $(VOXERA_UNITS)

services-disable:
	systemctl --user disable --now $(VOXERA_UNITS)

vera-start:
	systemctl --user daemon-reload
	systemctl --user start $(VERA_SERVICE)

vera-stop:
	systemctl --user stop $(VERA_SERVICE)

vera-restart:
	systemctl --user daemon-reload
	systemctl --user restart $(VERA_SERVICE)

vera-status:
	systemctl --user --no-pager status $(VERA_SERVICE)

vera-logs:
	journalctl --user -u $(VERA_SERVICE) -n 100 --no-pager

premerge: full-validation-check
