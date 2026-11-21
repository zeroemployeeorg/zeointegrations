# All build and test targets run only on an admitted build host.
.DEFAULT_GOAL := help
PYTHON ?= python3
UV ?= uv
MYPY ?= mypy
export UV MYPY

.PHONY: help help-all doctor hooks setup verify lint format-check format types test test-installed test-providers test-floor test-packages build check-tree run release clean

help: ## Show the first-time guide and five starting commands
	@printf '%s\n' 'zeo-integrations: one Python library with optional provider SDKs.' '' 'Start here:' '  make doctor     Check prerequisites without changing anything' '  make hooks      Install the lightweight main-push guard' '  make setup      Install the frozen development environment' '  make test       Run root offline fixture tests' '  make verify     Run the complete qualification gate' '' 'Run setup, tests and builds only on an admitted build host.' 'FLOOR_PYTHON names an existing Python 3.12 executable for the gate.' 'No provider credentials, services or mounted volumes are required.' 'make help-all lists every target; see docs/development.md for scopes and fixes.'

help-all: ## List every documented target
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' Makefile

doctor: ## Read-only prerequisite, hook and optional network diagnostics
	@$(PYTHON) -B tools/repo_maintenance.py doctor

hooks: ## Install the main-push guard without replacing existing hooks
	@$(PYTHON) -B tools/repo_maintenance.py hooks

setup: ## Install frozen development extras and the existing locked build backend
	$(UV) sync --frozen --all-extras --group release

lint: ## Check all repository Python with locked Ruff
	$(UV) run --frozen ruff check .

format-check: ## Check formatting without changing source
	$(UV) run --frozen ruff format --check .

format: ## Format repository Python explicitly during development
	$(UV) run --frozen ruff format .

types: ## Strict static types for the maintenance tool (mypy 2.3.1)
	$(MYPY) --version | grep -E '^mypy 2\.3\.1([ .]|$$)' >/dev/null
	$(MYPY) --strict --no-incremental --cache-dir=/dev/null --follow-imports=skip tools/repo_maintenance.py

test: ## Run root offline fixture and contract tests
	$(UV) run --frozen python -m unittest discover -s tests -v

test-installed: ## Check installed distribution behavior and import boundaries
	$(UV) run --frozen python -m unittest discover -s tests_installed -v

test-providers: ## Run every travelling provider test with synthetic transports
	$(UV) run --frozen python tools/verify_provider_tests.py

test-floor: ## Qualify the Python 3.12 floor using existing FLOOR_PYTHON
	$(UV) run --frozen python tools/verify_python_floor.py

test-packages: ## Run complete historical Loops, Kit and HubSpot gates
	$(MAKE) -C packages/loops verify
	$(MAKE) -C packages/kit verify
	$(MAKE) -C packages/hubspot verify

build: ## Build aggregate wheel and source archive offline without sources
	$(UV) build --offline --no-sources

check-tree: ## Check whitespace across the complete committed source tree
	git diff --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD

verify: ## Serial full gate: lint, format, types, fixtures, providers, floor and artifacts
	$(MAKE) lint
	$(MAKE) format-check
	$(MAKE) types
	$(MAKE) test
	$(MAKE) test-installed
	$(MAKE) test-providers
	$(MAKE) test-floor
	$(MAKE) build
	$(UV) run --frozen python tools/verify_loops_wheel.py
	$(MAKE) test-packages
	$(MAKE) check-tree

run: ## Inspect installed library metadata without a provider operation
	$(UV) run --frozen python -c 'from importlib.metadata import version; print("zeo-integrations", version("zeo-integrations"))'

release: verify ## Prepare qualified archives only; publication requires its normal authorization
	@printf '%s\n' 'Qualified archives are in dist/. No upload, tag or deployment performed.'

clean: ## Preview disposable outputs; CLEAN_CONFIRM=1 explicitly removes them
	@$(PYTHON) -B tools/repo_maintenance.py clean
