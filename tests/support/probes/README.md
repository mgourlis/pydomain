# Audit verification probes

These scripts are the probes that verified the bug audit (`docs/others/bug-audit-plan.md`). Each one
reproduces one or more audit rows (`DDD-*`, `CQRS-*`, `SAGA-*`, `ES-*`, `INF-*`, `FAKE-*`, `NEW-*`) and
prints what the library does today. They print observations; they do not assert.

They are reference material for Wave 1 of the bug-fix plan (`docs/others/bug-fix-plan.md`): a fix
rewrites the probe for its rows as a test against the public API (§4.2, "Regression example").

| Folder | Area |
|---|---|
| `ddd/` | Aggregates, entities, events, value objects, specifications, repositories |
| `cqrs/` | Buses, unit of work, outbox and relay, locks, idempotency |
| `saga/` | Saga manager, declarative sagas, recovery |
| `es/` | Event-sourced repository, upcasting, subscriptions |
| `inf/` | Bootstrap, `Application`, inbound and outbound adapters, registries |
| `fakes/` | The fakes in `pydomain.testing` |

## Rules

- Synthetic data only (orders, invoices, shipments).
- Not collected by pytest (`collect_ignore_glob` in `tests/conftest.py`) and excluded from ruff
  (`extend-exclude`) and mypy (`exclude`) in `pyproject.toml`. They are kept as written during the
  audit.
- **Deleted after Wave 1**, once every row they cover has a regression test.

## Running one

Run a probe from its own folder, so that `common.py` next to it is importable:

```bash
cd tests/support/probes/cqrs
PYTHONPATH=../../../../src python p_known.py
```
