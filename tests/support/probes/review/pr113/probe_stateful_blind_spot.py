"""Does the PR's state machine notice a relay that marks a failed delivery published?

Patches OutboundEventGateway.run_once so a broker failure is marked published
(the very defect "marked published only after the broker accepts it" forbids),
then runs the machine. If it passes, the machine lacks the invariant
published ⊆ accepted.
"""
from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

from pydomain.infrastructure import message_broker as mb
from tests.infrastructure.test_outbox_relay_stateful import OutboxRelayMachine

orig = mb.OutboundEventGateway._deliver


async def lossy(self, entry):
    await orig(self, entry)
    return None  # pretend every delivery succeeded


mb.OutboundEventGateway._deliver = lossy
try:
    run_state_machine_as_test(OutboxRelayMachine, settings=settings(max_examples=500, stateful_step_count=30, deadline=None, database=None))
    print("BLIND SPOT: machine passed with a relay that marks failed deliveries published")
except AssertionError as exc:
    print("caught:", str(exc).splitlines()[0][:200])
