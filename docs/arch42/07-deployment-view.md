# 7. Deployment View

This section describes how the `pydomain` library is packaged, distributed, and integrated into consumer projects. Because `pydomain` is a library — not a standalone application — the deployment view focuses on the single distribution artifact, its contents, and the runtime environment it expects.

---

## 7.1 Distribution Artifact

A single package published to PyPI (or a private index):

| Attribute | Value |
|-----------|-------|
| **Package name** | `pydomain` |
| **Version** | `0.1.0` (semantic versioning) |
| **Build backend** | `hatchling` |
| **Wheel target** | `src/pydomain/` |
| **Python support** | ≥ 3.12 |
| **Runtime dependencies** | `pydantic >= 2.12.5`, `uuid-utils >= 0.10` |
| **Type checking** | PEP 561 — `py.typed` marker included |
| **License** | See `LICENSE` |

### Installation

```bash
pip install pydomain
```

No extras, no optional feature flags, no per-module packages. All five modules (`ddd`, `cqrs`, `es`, `infrastructure`, `testing`) ship together in a single wheel.

### Installed structure

```
site-packages/
  pydomain/
    py.typed                          ← PEP 561 marker
    __init__.py                       ← Re-exports all public types
    ddd/                              ← Tactical DDD primitives
      __init__.py
      aggregate_root.py
      domain_event.py
      entity.py
      value_object.py
      ...
    cqrs/                             ← CQRS buses, commands, queries, pipeline
      __init__.py
      command_bus.py
      query_bus.py
      commands.py
      queries.py
      behaviors.py
      unit_of_work.py
      saga/
        __init__.py
        ...
    es/                               ← Event sourcing
      __init__.py
      aggregate.py
      event_store.py
      event_sourced_repository.py
      projection.py
      upcasting.py
      ...
    infrastructure/                   ← Cross-cutting wiring
      __init__.py
      message_bus.py
      bootstrap.py
      event_registry.py
      subscription.py
      ...
    testing/                          ← Test doubles (fakes)
      __init__.py
      ...
```

---

## 7.2 Dependency Graph at Deployment Time

The library has a minimal dependency footprint. No database drivers, no message broker clients, no DI containers — those are supplied by the consuming application.

```
┌─────────────────────────────────────────────────────────┐
│                  Consumer Application                    │
│                                                         │
│   ┌───────────┐  ┌──────────────┐  ┌────────────────┐  │
│   │ Domain    │  │ Application  │  │ Infrastructure │  │
│   │ (pydomain │  │ (pydomain    │  │ (SQLAlchemy,   │  │
│   │  .ddd)    │  │  .cqrs)      │  │  RabbitMQ,     │  │
│   │           │  │              │  │  PostgreSQL)    │  │
│   └───────────┘  └──────────────┘  └────────────────┘  │
│                                                         │
│   pip install pydomain                                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │      pydomain       │
              │                     │
              │  ┌───────────────┐  │
              │  │   pydantic    │  │
              │  │    >= 2.7     │  │
              │  └───────────────┘  │
              │  ┌───────────────┐  │
              │  │  uuid-utils   │  │
              │  │    >= 0.9     │  │
              │  └───────────────┘  │
              └─────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │   Python >= 3.12    │
              └─────────────────────┘
```

### Why so few dependencies

| Dependency | Purpose | Why it's required |
|------------|---------|-------------------|
| `pydantic >= 2.12.5` | Base model for all domain concepts, validation, serialization | Core mechanism (§4.2 S1) — the library cannot function without it |
| `uuid-utils >= 0.10` | Fast UUIDv7 generation for `event_id` and aggregate IDs | Domain events require time-ordered UUIDs; `uuid-utils` is the fastest pure-Python option |
| `pytest >= 8.0` | Test runner | **Dev dependency only** — not installed in production |
| `anyio >= 4.0` | Async test backend | **Dev dependency only** |
| `ruff >= 0.4` | Linter / formatter | **Dev dependency only** |

---

## 7.3 Runtime Environment

### Consumer-side wiring

The library does **not** auto-configure itself. The consuming application is responsible for wiring via the `bootstrap()` composition root:

```
Consumer Application
│
├── main.py / app.py
│   │
│   ├── 1. Create infrastructure adapters and the bus
│   │      (EventStore, SnapshotStore, MessageBroker, etc.)
│   │      bus = MessageBus()
│   │      gateway = InboundEventGateway(subscriber, bus)   # same bus
│   │
│   ├── 2. bootstrap(
│   │        event_store=...,
│   │        snapshot_store=...,
│   │        message_bus=bus,          # required with inbound gateways
│   │        message_broker=...,
│   │        event_registry=...,
│   │        inbound_gateways=[gateway],
│   │      )
│   │      → returns Application (ValueError if a gateway is on another bus)
│   │
│   ├── 3. Register handlers on app.message_bus
│   │      app.message_bus.register_command(CreateOrder, handle_create_order, uow_factory)
│   │      app.message_bus.register_query(GetOrder, handle_get_order)
│   │      app.message_bus.register_event(OrderCreated, handle_order_created)
│   │
│   └── 4. Start subscription runners (if using projections)
│          runner = MySubscriptionRunner(event_store, checkpoint_store)
│          runner.add_subscription(sub)
│          asyncio.create_task(runner.run())
│
└── tests/
    │
    ├── Uses pydomain.testing fakes
    │      from pydomain.testing import (
    │          FakeEventStore, FakeUnitOfWork, FakeRepository, ...
    │      )
    │
    └── Calls bootstrap() with fakes
           bootstrap(event_store=FakeEventStore(), ...)
```

On exit the application calls `await app.shutdown()`, which stops the inbound gateways, the outbound gateway and the broker, in that order, and runs every stop even when one raises. A `bootstrap()` whose start fails part-way stops what it had started before re-raising ([ADR-073](../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

### Integration patterns

The library integrates at **three points** only:

| Integration point | Consumer provides | Library provides |
|-------------------|-------------------|------------------|
| **Persistence** | `EventStore`, `SnapshotStore`, `CheckpointStore` implementations | `Protocol` interfaces — implement against the shape |
| **Messaging** | `MessageBroker` implementation | `IntegrationEvent` base class, `EventRegistry` for serialization |
| **Framework binding** | `bootstrap()` call with adapters | `Application` facade with `dispatch()` |

No database driver, no ORM, no web framework is assumed. The consumer chooses the technology and writes a thin adapter layer that satisfies the `Protocol` interfaces.

---

## 7.4 Build and Release Pipeline

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Developer   │    │   CI / CD    │    │    PyPI      │    │   Consumer   │
│               │    │              │    │              │    │              │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                    │                    │                    │
       │ 1. git push        │                    │                    │
       ├───────────────────►│                    │                    │
       │                    │                    │                    │
       │                    │ 2. ruff check      │                    │
       │                    │    mypy            │                    │
       │                    │    pytest          │                    │
       │                    │                    │                    │
       │                    │ 3. hatch build     │                    │
       │                    │    (wheel + sdist)  │                    │
       │                    │                    │                    │
       │                    │ 4. publish to PyPI │                    │
       │                    ├───────────────────►│                    │
       │                    │                    │                    │
       │                    │                    │ 5. pip install     │
       │                    │                    │     pydomain       │
       │                    │                    │◄───────────────────┤
       │                    │                    │                    │
```

| Stage | Tool | What happens |
|-------|------|-------------|
| **Matrix** | GitHub Actions (`test.yml`) | Lint, format check, type check and tests run on Python 3.12, 3.13 and 3.14 — every version the classifiers claim |
| **Floor** | GitHub Actions (`test.yml`, job `floor`) | Installs the runtime dependencies at the floors `pyproject.toml` declares, checks the installed versions match them, and runs the tests on Python 3.12 and 3.14 (B-75, B-77) |
| **Lint** | `ruff` (target `py312`) | Style + error checks |
| **Type check** | `mypy` (`python_version = "3.12"`, run on each matrix version) | Full type coverage; `pydantic.mypy` plugin enabled |
| **Test** | `pytest` + `pytest-anyio` | Async tests with branch coverage |
| **Build** | `hatchling` | Produces wheel and sdist from `src/pydomain/` |
| **Publish** | `hatch` or `twine` | Upload to PyPI |

---

## 7.5 Versioning and Compatibility

| Aspect | Policy |
|--------|--------|
| **Version scheme** | Semantic versioning (`MAJOR.MINOR.PATCH`) |
| **Breaking changes** | `MAJOR` bump — e.g. removing a public type, changing a `Protocol` signature |
| **New features** | `MINOR` bump — e.g. adding a new pipeline behavior, new base class |
| **Bug fixes** | `PATCH` bump — no API changes |
| **Python version** | Minimum 3.12; every version listed in the classifiers (3.12–3.14) is tested in CI; no backport guarantees |
| **Pydantic** | v2 only — no v1 compatibility shims |
| **Event schema evolution** | Consumer-managed via `EventUpcaster` chain (§6.3) — the library does not version events itself |
