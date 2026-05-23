# Event Sourcing Concepts

> **Adoption Level:** 4

Understanding-oriented documentation for the Event Sourcing layer of pydomain.

## Pages

| Page | Topic |
|------|-------|
| [Event Sourcing](event-sourcing.md) | The core pattern — state as a sequence of events |
| [Event-Sourced Aggregates](event-sourced-aggregates.md) | Aggregates built from event streams via apply/replay |
| [Event Stream](event-stream.md) | The frozen read-only representation of a stream slice |
| [Event Store](event-store.md) | The append-only persistence protocol |
| [Projections](projections.md) | Event-sourced projections with checkpoint tracking |
| [Event Versioning](event-versioning.md) | Schema evolution and upcasting for immutable events |
| [Event-Sourced Repositories](event-sourced-repositories.md) | Repositories that persist via events with optimistic concurrency |
| [Snapshots](snapshots.md) | Point-in-time aggregate state capture for fast rebuild |
| [Subscriptions](subscriptions.md) | Durable catch-up event processing with checkpoint tracking |

## How-Tos

See [How-to / Event Sourcing](../../how-to/event-sourcing/) for step-by-step guides.
