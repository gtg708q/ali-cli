# ali-cli — developer Makefile
# Run `make help` for the full list.

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

.PHONY: help install dev playwright test lint fmt clean distclean

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install the CLI + runtime deps (editable)
	$(PIP) install -e .

dev:  ## Install dev deps too (pytest, ruff)
	$(PIP) install -e ".[dev]"

playwright:  ## Install Chromium browser for Playwright
	$(PYTHON) -m playwright install chromium

test:  ## Run end-to-end CLI tests (requires an active session from `ali login`)
	$(PYTHON) tests/test_e2e.py

lint:  ## Ruff lint
	$(PYTHON) -m ruff check ali_cli scripts tests

fmt:  ## Ruff auto-format + import sort
	$(PYTHON) -m ruff check --fix ali_cli scripts tests
	$(PYTHON) -m ruff format ali_cli scripts tests

clean:  ## Remove Python build artifacts
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

distclean: clean  ## clean + drop local user state (DESTRUCTIVE — clears session cookies)
	rm -rf ~/.ali-cli
