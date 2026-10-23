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
| `saga/` | Saga manager, declarative sagas, recovery (DCE-120) |
| `es/` | Event-sourced repository, upcasting, subscriptions |
| `inf/` | Bootstrap, `Application`, inbound and outbound adapters, registries |
| `fakes/` | The fakes in `pydomain.testing` |
| `review/` | The Wave 1 review probes still waiting for a test (see its README) |

## Rules

- Synthetic data only (orders, invoices, shipments).
- Not collected by pytest (`collect_ignore_glob` in `tests/conftest.py`) and excluded from ruff
  (`extend-exclude`) and mypy (`exclude`) in `pyproject.toml`. They are kept as written during the
  audit.
- **Deleted after Wave 1**, once every row they cover has a regression test. DCE-127 deleted the scripts
  whose rows all have one. The scripts left reproduce at least one row that is still open: a Wave 2 or
  Wave 3 cluster, a saga row of DCE-120, or a gap no cluster owns yet. Their committed tests come with the
  fix of that row; a test that pinned the defect now would bless it (decision-log P-28).

## Owners of the scripts left

Each script stays until the owner of every row it reproduces has fixed it (decision-log P-28, P-31).
`common.py` in a folder holds shared fixtures.

| Script | Open rows → owner |
|---|---|
| `cqrs/a17_types.py` | A17 → W2-11 |
| `cqrs/p_04_09.py` | CQRS-05, A31 → W2-2; CQRS-09 → W2-10 (CQRS-04 fixed by DCE-146; CQRS-07 fixed by DCE-145) |
| `cqrs/p_10_14.py` | CQRS-12 → W2-10; CQRS-13, A16 → W3-1 (CQRS-11 fixed by DCE-143) |
| `cqrs/p_15_25.py` | CQRS-17 → W2-8; CQRS-18, CQRS-27 → W2-3; CQRS-19 → W2-10; CQRS-23 → W2-9; CQRS-20 (cancel and formatter parts) → W2-3 (CQRS-21 fixed by DCE-144) |
| `cqrs/p_22.py` | CQRS-22 → W2-7 (docs) |
| `cqrs/p_known.py` | CQRS-K2 → W2-2; CQRS-K4 → W3-1 (CQRS-K1, CQRS-K3 fixed by DCE-143) |
| `ddd/p01_aggregate.py` | DDD-02, DDD-20 → W2-9; A13 → W2-6 |
| `ddd/p02_entity.py` | DDD-06, DDD-12 → W2-9; DDD-07 → W2-12 (DDD-08 fixed by DCE-128) |
| `ddd/p03_event_vo.py` | DDD-11, DDD-13 → W2-12; A18, A19 → W1-11 (docs) |
| `ddd/p04_spec.py` | DDD-21 → W2-11 |
| `ddd/p05_repo_import.py` | DDD-16, DDD-17 → W2-11; DDD-19 → W2-6 |
| `ddd/p06_new.py` | FAKE-13 → W2-6; FAKE-12 → DCE-120; CQRS-26 → W2-10 |
| `es/probe_agg_upcast.py` | ES-07, ES-25 → W2-7 |
| `es/probe_repo.py` | ES-01, ES-02, ES-10, ES-11 → W2-6; ES-06 → W2-7 |
| `es/probe_runloop.py` | ES-19 → W2-7 |
| `es/probe_subs.py` | ES-18, ES-19, ES-21, ES-22 → W2-7; ES-23 → W2-8 |
| `fakes/p05_outbox.py` | FAKE-05 (rest): the duplicate `message_id` → W2-2 (ADR-080 point 8); marks on unknown ids and fetch order → open (bug-fix-plan.md §14, D-FAKE-05) |
| `fakes/p08_09_broker_sub.py` | FAKE-08 → W2-7; FAKE-09 (rest) → W2-3 |
| `fakes/probe_imports.py`, `fakes/probe_import_chain.py` | DDD-17 → W2-11 |
| `fakes/probe_repo_uow.py` | FAKE-01, FAKE-02 → W2-6; FAKE-11 → W2-5; FAKE-04, a rollback keeps the saves in `FakeRepository` → W2-6 (ADR-068, P-31) (FAKE-04's commit-failure injection fixed by DCE-143: `FakeUnitOfWork(fail_at=...)`) |
| `inf/bootstrap_probe.py` | INF-05, INF-07 (rest), INF-08 → W2-4 |
| `inf/inbound_probe.py` | INF-01 → W2-3; INF-02 → W2-2; INF-03, INF-04 → W2-8 |
| `inf/outbound_probe.py` | INF-11 (rest) → W2-4 |
| `saga/*` | The saga rows → DCE-120 |
| `review/*` | See `review/README.md` |

## Running one

Run a probe from its own folder, so that `common.py` next to it is importable:

```bash
cd tests/support/probes/cqrs
PYTHONPATH=../../../../src python p_known.py
```
