# Concepts

Understanding-oriented documentation — the *why* behind pydomain's design decisions.

Each subfolder maps to a module in `src/pydomain/` and explains the concepts it provides.

## Sections

| Section | Topic | Adoption Level |
|---------|-------|---------------|
| [DDD](ddd/) | Entities, Value Objects, Aggregates, Domain Events, Repositories, Specifications, Factories, Domain Services | Level 1 |
| [CQRS](cqrs/) | Commands, Queries, Handlers, Buses, Pipeline Behaviors, Unit of Work, Integration Events, Idempotency | Level 2–3 |
| [Event Sourcing](es/) | Event Store, Projections, Snapshots, Upcasting, Subscriptions | Level 4–5 |
| [Sagas](sagas/) | Saga orchestration, state, compensation, lifecycle | Level 5 |
| [Infrastructure](infrastructure/) | Bootstrap, Event Registry, Message Broker | Level 3+ |
| [Testing](testing/) | Testing philosophy and structure | All levels |
