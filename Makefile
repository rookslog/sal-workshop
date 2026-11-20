# The gate is `make check`. It uses the project's own environment directly, so it also runs where
# `uv` cannot reach its cache. Create the environment once with `make setup`.
PY := .venv/bin/python

.PHONY: setup check lint format-check types test leak

setup:
	uv sync

check: lint format-check types test leak

$(PY):
	@echo "No environment: run 'make setup' first." >&2; exit 1

lint: $(PY)
	$(PY) -m ruff check .

format-check: $(PY)
	$(PY) -m ruff format --check .

types: $(PY)
	$(PY) -m mypy

test: $(PY)
	$(PY) -m pytest

leak: $(PY)
	$(PY) tools/leak_check.py
