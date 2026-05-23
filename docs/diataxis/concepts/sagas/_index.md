# Sagas

> **Adoption Level:** 5 — Sagas & Process Managers

Understanding-oriented documentation for long-running business transactions — coordination, compensation, and lifecycle management.

## Pages

| Page | Topic |
|------|-------|
| [Saga](saga.md) | Base class, event handling, declarative vs imperative style |
| [Declarative vs Imperative](declarative-vs-imperative.md) | Choosing between command-mapper and handler styles |
| [Saga State](saga-state.md) | Lifecycle tracking, idempotency, step history, compensation stack |
| [Saga Manager](saga-manager.md) | Orchestration: load → handle → save → dispatch |
| [Saga Registry](saga-registry.md) | Mapping event types to saga classes |
| [Saga Compensation](saga-compensation.md) | LIFO compensation stack and execution |
| [Saga Lifecycle](saga-lifecycle.md) | State machine: PENDING → RUNNING → SUSPENDED → TERMINAL |
| [Saga Repository](saga-repository.md) | Persistence contract for saga state |
| [Saga Error Handling](saga-error-handling.md) | Failure modes, retry exhaustion, compensation dispatch |
