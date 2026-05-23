# Saga How-to Guides

> **Adoption Level:** 5 — Sagas & Process Managers

Task-oriented guides for building, configuring, and operating sagas.

## Guides

| Guide | Task |
|-------|------|
| [Define a Saga](define-saga.md) | Create a saga class with event handlers and compensation |
| [Hydrate Saga State](saga-state-hydration.md) | Reconstruct commands from serialized saga records |
| [Configure a Saga Manager](configure-saga-manager.md) | Wire registry, repository, and command bus |
| [Implement Compensation](saga-compensation.md) | Register and execute compensating actions |
| [Suspend, Resume & Timeout](saga-suspend-resume-timeout.md) | Human-in-the-loop patterns with timeouts |
| [Handle Saga Errors](saga-error-handling.md) | Failure modes, retry, and recovery |
| [Prune Saga History](saga-pruning.md) | Cap unbounded step history and event tracking |

## See Also

- [Concepts / Sagas](../../concepts/sagas/) — understanding saga architecture
- [Testing / Fake Saga Repository](../testing/use-fake-saga-repository.md) — in-memory test double
