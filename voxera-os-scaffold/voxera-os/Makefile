.PHONY: install dev panel test lint fmt e2e update update-fast services-install services-restart services-status services-stop services-disable

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
SYSTEMD_SRC_DIR := deploy/systemd/user
VOXERA_UNITS := voxera-daemon.service voxera-panel.service

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

update:
	bash scripts/update.sh --smoke

update-fast:
	bash scripts/update.sh --skip-tests

services-install:
	mkdir -p "$(SYSTEMD_USER_DIR)"
	cp "$(SYSTEMD_SRC_DIR)"/*.service "$(SYSTEMD_USER_DIR)/"
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
