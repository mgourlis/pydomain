# Installation

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | ≥ 3.12 |
| Pydantic | ≥ 2.7 |

pydomain uses Python 3.12+ features (generic type parameters, `type` aliases) and Pydantic v2 APIs exclusively.

> ⚠️ **Not compatible with Pydantic v1.** pydomain uses `ConfigDict`, `model_copy()`, `model_validate()`, `@field_validator`, and `@model_validator` — all Pydantic v2 APIs. There is no v1 compatibility shim.

## Install

```bash
pip install pydomain
```

This pulls in `pydantic>=2.7` and `uuid-utils>=0.9` automatically.

## Verify

```python
from pydomain.ddd.value_object import ValueObject
from pydomain.ddd.entity import Entity
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent

print("pydomain installed successfully")
```

## Development Dependencies

For testing and linting, install the dev extras:

```bash
pip install "pydomain[dev]"
```

This adds:

| Package | Purpose |
|---------|---------|
| `pytest` + `pytest-anyio` | Async test runner |
| `pytest-cov` | Coverage reporting |
| `ruff` | Linter and formatter |
| `mypy` | Static type checking |
| `pre-commit` | Git hook management |

## What's Next?

→ [Quickstart tutorial](quickstart.md) — build your first aggregate and command.
