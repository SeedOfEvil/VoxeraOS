.PHONY: install dev panel test test-failed-sidecar type-check type-check-strict update-mypy-baseline quality-check release-check merge-readiness-check full-validation-check lint fmt e2e update update-fast services-install services-restart services-status services-stop services-disable premerge

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
SYSTEMD_SRC_DIR := deploy/systemd/user
VOXERA_UNITS := voxera-daemon.service voxera-panel.service
VOXERA_PROJECT_DIR := $(abspath .)

install:
	python -m pip install -U pip
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install --hook-type pre-commit --hook-type pre-push || true

panel:
	voxera panel

test:
	pytest

test-failed-sidecar:
	pytest -q tests/test_queue_daemon.py -k "failed_sidecar_schema_version_policy_rejects_unknown_future_version or queue_failure_lifecycle_smoke_sidecar_snapshot_then_prune"

type-check:
	python scripts/mypy_ratchet.py

# Strict typing target for full-baseline cleanup workstreams.
type-check-strict:
	mypy src/voxera --ignore-missing-imports

update-mypy-baseline:
	python scripts/mypy_ratchet.py --write-baseline

quality-check:
	ruff format --check .
	ruff check .
	$(MAKE) type-check

release-check:
	pytest -q \
		tests/test_version_source.py \
		tests/test_panel.py::test_panel_app_uses_shared_version_source \
		tests/test_docs_consistency.py \
		tests/test_cli_version.py::test_root_version_option_prints_and_exits

merge-readiness-check:
	$(MAKE) quality-check
	$(MAKE) release-check

# Broader local validation pass (not required on every pull request).
full-validation-check:
	$(MAKE) merge-readiness-check
	$(MAKE) test-failed-sidecar
	pytest -q
	bash scripts/e2e_smoke.sh

lint:
	$(MAKE) quality-check

fmt:
	ruff format .

e2e:
	bash scripts/e2e_smoke.sh

update:
	bash scripts/update.sh --smoke

update-fast:
	bash scripts/update.sh --skip-tests

services-install:
	mkdir -p "$(SYSTEMD_USER_DIR)"
	for unit in voxera-daemon.service voxera-panel.service; do \
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

premerge:
	$(MAKE) full-validation-check
