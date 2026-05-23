# CQRS Concepts

> **Adoption Level:** 2–3

Understanding-oriented documentation for the CQRS (Command Query Responsibility Segregation) layer of pydomain.

## Pages

| Page | Topic |
|------|-------|
| [Commands](commands.md) | Expressing intent — the write side |
| [Queries](queries.md) | Asking questions — the read side |
| [Command & Query Result Types](command-query-result-types.md) | Type-safe results for the message pipeline |
| [Handlers](handlers.md) | Protocol-based handler patterns for commands, queries, and events |
| [Command Bus](command-bus.md) | Routing commands with transactional lifecycle |
| [Query Bus](query-bus.md) | Routing queries without transactional overhead |
| [Unit of Work](unit-of-work.md) | Transactional boundary and event collection |
| [Pipeline Behaviors](pipeline-behaviors.md) | Onion-middleware for cross-cutting concerns |
| [Integration Events](integration-events.md) | Cross-boundary events with primitive-only payloads |
| [Idempotency & Locking](idempotency-and-locking.md) | Duplicate detection and concurrency control |
| [Read Models](read-models.md) | Query-optimized projections from domain events |

## How-Tos

See [How-to / CQRS](../../how-to/cqrs/) for step-by-step guides.
