.PHONY: help test lint format type check install pre-commit clean mutate

help:
	@echo "pydomain development tasks"
	@echo ""
	@echo "  make test          Run pytest with coverage"
	@echo "  make lint          Run ruff linter"
	@echo "  make format        Format code with ruff"
	@echo "  make type          Type check with mypy"
	@echo "  make check         Run lint + type checks"
	@echo "  make mutate MODULE=src/pydomain/<path>.py"
	@echo "                     Mutation-test one module with mutmut"
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

# Mutants are generated for all of src/pydomain, but only MODULE's are run.
# The summary counts MODULE's mutants by status; the score is killed / total.
MUTANT_MODULE = $(subst /,.,$(patsubst src/%.py,%,$(MODULE)))

mutate:
	@test -n "$(MODULE)" || { echo "usage: make mutate MODULE=src/pydomain/<path>.py"; exit 2; }
	@test -f "$(MODULE)" || { echo "no such module: $(MODULE)"; exit 2; }
	python -m mutmut run "$(MUTANT_MODULE).*"
	@echo "Mutants in $(MODULE):"
	@python -m mutmut results --all true | grep -F " $(MUTANT_MODULE)." \
		| awk '{ n[$$NF]++; t++ } END { for (s in n) print "  " s ": " n[s]; \
		printf "  score: %d/%d killed\n", n["killed"], t }'
	@python -m mutmut results | grep -F " $(MUTANT_MODULE)." || echo "  no surviving mutants"

check: lint type
	@echo "All checks passed!"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name .coverage -delete
	rm -rf mutants/
	rm -rf .venv/
