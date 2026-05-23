# Infrastructure How-to Guides

> **Adoption Level:** 3

Task-oriented guides for wiring the application infrastructure. Each guide solves a specific problem with copy-paste-ready steps.

## Wiring

| Guide | Task |
|-------|------|
| [Bootstrap the Application](bootstrap-application.md) | Wire all components into a configured `Application` |
| [Register Handlers](register-handlers.md) | Register command, query, and event handlers on the bus |
| [Configure a Message Broker](configure-message-broker.md) | Publish integration events to an external broker |
| [Configure a MessageSubscriber](configure-message-subscriber.md) | Receive integration events from an external broker |
| [Configure an InboundEventGateway](configure-inbound-event-gateway.md) | Bridge external broker messages to the internal message bus |
| [Use the Event Registry](event-registry.md) | Register event types and serialize/deserialize events |
| [Set Up Catch-Up Subscriptions](subscriptions.md) | Configure SubscriptionRunner for durable projection sync |

## See Also

- [CQRS How-to Guides](../cqrs/) — commands, queries, handlers, pipeline behaviors
- [Concepts / Infrastructure](../../concepts/infrastructure/) — understanding-oriented docs
