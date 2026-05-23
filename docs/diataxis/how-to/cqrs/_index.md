# CQRS How-to Guides

> **Adoption Level:** 2–3

Task-oriented guides for the CQRS layer. Each guide solves a specific problem with copy-paste-ready steps.

## Commands

| Guide | Task |
|-------|------|
| [Define a Command](define-command.md) | Subclass `Command[TResult]` with typed results |
| [Implement a Command Handler](implement-command-handler.md) | Write business logic with Unit of Work |
| [Configure the Command Bus](configure-command-bus.md) | Wire handlers, UoW factories, and behaviors |

## Queries

| Guide | Task |
|-------|------|
| [Define a Query](define-query.md) | Subclass `Query[TResult]` with typed results |
| [Implement a Query Handler](implement-query-handler.md) | Write read-side logic with read stores |
| [Configure the Query Bus](configure-query-bus.md) | Wire query handlers |

## Results

| Guide | Task |
|-------|------|
| [Command & Query Result Types](command-result-types.md) | Define typed return values for handlers |

## Pipeline & Infrastructure

| Guide | Task |
|-------|------|
| [Add a Pipeline Behavior](add-pipeline-behavior.md) | Create custom middleware |
| [Add Idempotency](add-idempotency.md) | Handle duplicate commands safely |
| [Add Distributed Locking](add-distributed-locking.md) | Prevent concurrent aggregate modification |

## Events & Projections

| Guide | Task |
|-------|------|
| [Handle Domain Events](handle-domain-events.md) | React to events after commit |
| [Implement an Integration Event](implement-integration-event.md) | Publish cross-boundary events |
| [Define a Read Store Protocol](define-read-store-protocol.md) | Decouple handlers from storage |
| [Implement a Read Store](implement-read-store.md) | Concrete read-side storage |

## Concepts

See [Concepts / CQRS](../../concepts/cqrs/) for understanding-oriented documentation.
