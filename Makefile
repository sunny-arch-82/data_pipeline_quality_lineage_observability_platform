# Load local development variables when .env exists. Docker Compose also
# reads this file automatically, but Make targets need the values exported.
 ifneq (,$(wildcard ./.env))
include .env
export
endif

PYTHON ?= python3.13
VENV ?= .venv

VENV_PYTHON := $(VENV)/bin/python
VENV_PIP    := $(VENV_PYTHON) -m pip
DBT         := $(VENV)/bin/dbt
PYTEST      := $(VENV)/bin/pytest
EDR         := $(VENV)/bin/edr
RUFF        := $(VENV)/bin/ruff
DEPS_STAMP  := $(VENV)/.deps-installed

.PHONY: help setup run test report lint validate-env venv deps clean-venv python-version

help:
	@echo "Available targets:"
	@echo "  make setup       Create .venv, install dependencies, prepare dbt/Elementary"
	@echo "  make run         Run the dbt/OpenLineage pipeline"
	@echo "  make test        Run unit + property tests"
	@echo "  make report      Generate the Elementary report"
	@echo "  make lint        Run Ruff"
	@echo "  make validate-env Validate .env configuration"
	@echo "  make clean-venv  Remove the local virtual environment"
	@echo ""
	@echo "Python executable: $(PYTHON)"

python-version:
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
		echo "ERROR: $(PYTHON) was not found on PATH."; \
		echo "Your requested runtime is Python 3.13.5."; \
		exit 1; \
	}
	@$(PYTHON) --version

# ── Python virtual environment ─────────────────────────────────────────────
$(VENV_PYTHON):
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
		echo "ERROR: $(PYTHON) was not found on PATH."; \
		exit 1; \
	}
	@echo ">>> Creating virtual environment with $(PYTHON)..."
	@$(PYTHON) -m venv $(VENV) || { \
		echo "ERROR: Could not create $(VENV)."; \
		echo "On Debian, install venv support for Python 3.13 (for example: sudo apt install python3.13-venv) and retry."; \
		exit 1; \
	}
	@echo ">>> Upgrading pip/setuptools/wheel inside $(VENV)..."
	@$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel

venv: $(VENV_PYTHON)

# Reinstall project dependencies only when pyproject.toml changes or the
# virtual environment is recreated.
$(DEPS_STAMP): pyproject.toml $(VENV_PYTHON)
	@echo ">>> Installing Python dependencies into $(VENV)..."
	@$(VENV_PIP) install -e ".[dev]"
	@touch $(DEPS_STAMP)

deps: $(DEPS_STAMP)

# ── Environment validation ─────────────────────────────────────────────────
validate-env: venv
	@$(VENV_PYTHON) scripts/validate_env.py

# ── Bootstrap ──────────────────────────────────────────────────────────────
setup: deps validate-env
	@echo ">>> Installing dbt packages..."
	@$(DBT) deps --project-dir dbt_project --profiles-dir dbt_project
	@echo ">>> Initialising Elementary..."
	@$(DBT) run --select elementary --project-dir dbt_project --profiles-dir dbt_project
	@echo ">>> Validating Marquez connection..."
	@$(VENV_PYTHON) scripts/check_marquez.py
	@echo ">>> Setup complete."
	@echo ">>> Virtual environment: $(VENV)"

# ── Pipeline ───────────────────────────────────────────────────────────────
run: deps validate-env
	@echo ">>> Running dbt-ol pipeline..."
	@$(VENV_PYTHON) src/run_pipeline.py

# ── Quality ────────────────────────────────────────────────────────────────
test: deps
	@$(PYTEST) tests/unit tests/property -v

# ── Reporting ──────────────────────────────────────────────────────────────
report: deps
	@echo ">>> Generating Elementary report..."
	@$(EDR) report generate --project-dir dbt_project --profiles-dir dbt_project
	@echo ">>> Copying report to reports/..."
	@mkdir -p reports
	@$(VENV_PYTHON) -c "import shutil, glob; [shutil.copy(f, 'reports/') for f in glob.glob('edr_target/*.html')]"

# ── Linting ────────────────────────────────────────────────────────────────
lint: deps
	@$(RUFF) check src tests

# ── Cleanup ────────────────────────────────────────────────────────────────
clean-venv:
	@echo ">>> Removing $(VENV)..."
	@rm -rf $(VENV)