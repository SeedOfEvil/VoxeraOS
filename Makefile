.PHONY: install dev panel test test-failed-sidecar quality-check release-check merge-readiness-check lint fmt e2e update update-fast services-install services-restart services-status services-stop services-disable

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
SYSTEMD_SRC_DIR := deploy/systemd/user
VOXERA_UNITS := voxera-daemon.service voxera-panel.service
VOXERA_PROJECT_DIR := $(abspath .)

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

test-failed-sidecar:
	pytest -q tests/test_queue_daemon.py -k "failed_sidecar_schema_version_policy_rejects_unknown_future_version or queue_failure_lifecycle_smoke_sidecar_snapshot_then_prune"

quality-check:
	ruff format --check .
	ruff check .
	mypy src/voxera --ignore-missing-imports

release-check:
	pytest -q \
		tests/test_version_source.py \
		tests/test_panel.py::test_panel_app_uses_shared_version_source \
		tests/test_docs_consistency.py \
		tests/test_cli_version.py::test_root_version_option_prints_and_exits

merge-readiness-check:
	$(MAKE) quality-check
	$(MAKE) release-check

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

.PHONY: premerge
premerge:
	@set -e; \
	echo "[premerge] failed-sidecar guardrail"; \
	$(MAKE) test-failed-sidecar; \
	echo "[premerge] unit tests"; \
	pytest -q; \
	echo "[premerge] e2e smoke"; \
	bash scripts/e2e_smoke.sh; \
	echo "[premerge] OK to merge"
