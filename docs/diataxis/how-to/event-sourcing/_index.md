# Event Sourcing How-Tos

> **Adoption Level:** 4

Task-oriented guides for implementing Event Sourcing with pydomain.

## Pages

| Page | Topic |
|------|-------|
| [Define an ES Aggregate](event-sourced-aggregate.md) | Implement the `_when`/`_apply` pattern |
| [Connect an Event Store](connect-event-store.md) | Wire an event store backend to the repository |
| [Implement an ES Repository](implement-es-repository.md) | Load and save event-sourced aggregates |
| [Create an ES Projection](create-es-projection.md) | Build read models from event streams |
| [Implement an Upcaster](implement-upcaster.md) | Transform events across schema versions |
| [Handle ES Errors](handle-es-errors.md) | Deal with concurrency conflicts, duplicate commands, and stale snapshots |
| [Configure Snapshots](configure-snapshots.md) | Wire snapshot store and policies for faster aggregate loads |
| [Track Checkpoints](track-checkpoints.md) | Persist subscription progress with CheckpointStore |

## Concepts

See [Concepts / Event Sourcing](../../concepts/es/) for understanding-oriented documentation.
