.PHONY: help test lint format type check install docs clean

help:
	@echo "pydomain development tasks"
	@echo ""
	@echo "  make test          Run pytest with coverage"
	@echo "  make lint          Run ruff linter"
	@echo "  make format        Format code with ruff"
	@echo "  make type          Type check with mypy"
	@echo "  make check         Run lint + type checks"
	@echo "  make install       Install dev dependencies"
	@echo "  make clean         Remove build artifacts and caches"

install:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

type:
	uv run mypy src

check: lint type
	@echo "All checks passed!"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name .coverage -delete
	rm -rf .venv/
