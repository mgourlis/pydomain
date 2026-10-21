# Installation

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | ≥ 3.12 |
| Pydantic | ≥ 2.12.5 |

pydomain uses Python 3.12+ features (generic type parameters, `type` aliases) and Pydantic v2 APIs exclusively.

> ⚠️ **Not compatible with Pydantic v1.** pydomain uses `ConfigDict`, `model_copy()`, `model_validate()`, `@field_validator`, and `@model_validator` — all Pydantic v2 APIs. There is no v1 compatibility shim.

## Install

```bash
pip install pydomain-lib
```

This pulls in `pydantic>=2.12.5` and `uuid-utils>=0.9` automatically.

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
pip install "pydomain-lib[dev]"
```

This adds:

| Package | Purpose |
|---------|---------|
| `pytest` + `pytest-anyio` | Async test runner |
| `pytest-cov` | Coverage reporting |
| `ruff` | Linter and formatter |
| `mypy` | Static type checking |
| `pre-commit` | Git hook management |
| `hypothesis` | Property-based and stateful tests |
| `mutmut` | Mutation testing |

## Contract Suites

To check your own adapters against the pydomain Protocols with the contract suites in `pydomain.testing.contracts`, install the testing extra:

```bash
pip install "pydomain-lib[testing]"
```

It adds `pytest>=8.0` and `anyio>=4.0`. Only the contract suites import them; `import pydomain` and `import pydomain.testing` do not. See [How to Run a Contract Suite Against Your Adapter](../how-to/testing/run-contract-suites.md).

## What's Next?

→ [Quickstart tutorial](quickstart.md) — build your first aggregate and command.
