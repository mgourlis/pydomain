# Infrastructure Concepts

> **Adoption Level:** 1–3

Understanding-oriented documentation for the infrastructure layer — bootstrap, message bus, event registry, message broker, message subscriber, inbound event gateway, and the exception hierarchy.

## Core Infrastructure

| Page | Topic |
|------|-------|
| [Message Bus](message-bus.md) | Central dispatch for commands, queries, and events |
| [Application Bootstrap](bootstrap.md) | Dependency-injection composition root |
| [Event Registry](event-registry.md) | Event type registration for serialization |

## Cross-Boundary Messaging

| Page | Topic |
|------|-------|
| [Message Broker](message-broker.md) | Protocol for publishing integration events (outbound) |
| [MessageSubscriber](message-subscriber.md) | Protocol for receiving integration events (inbound) |
| [InboundEventGateway](inbound-event-gateway.md) | Bridging external brokers to the internal message bus |

## Cross-Cutting

| Page | Topic |
|------|-------|
| [Exception Hierarchy](exception-hierarchy.md) | Domain error types and handling philosophy |

## Related Sections

See [Concepts / CQRS](../cqrs/) for command/query/handler/bus concepts and [Concepts / Event Sourcing](../es/) for event store and projection concepts.
