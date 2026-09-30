.PHONY: help test lint format type check install pre-commit clean

help:
	@echo "pydomain development tasks"
	@echo ""
	@echo "  make test          Run pytest with coverage"
	@echo "  make lint          Run ruff linter"
	@echo "  make format        Format code with ruff"
	@echo "  make type          Type check with mypy"
	@echo "  make check         Run lint + type checks"
	@echo "  make install       Install dev dependencies"
	@echo "  make pre-commit    Install pre-commit git hooks"
	@echo "  make clean         Remove build artifacts and caches"

install:
	python -m pip install -e ".[dev]"

pre-commit:
	python -m pre_commit install

test:
	python -m pytest

lint:
	python -m ruff check src tests

format:
	python -m ruff format src tests

type:
	python -m mypy src

check: lint type
	@echo "All checks passed!"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name .coverage -delete
	rm -rf .venv/
