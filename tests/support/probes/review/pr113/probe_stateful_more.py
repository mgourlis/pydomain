"""Run the PR's outbox state machine with many more examples and longer runs."""
import sys
from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

from tests.infrastructure.test_outbox_relay_stateful import OutboxRelayMachine

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
run_state_machine_as_test(
    OutboxRelayMachine,
    settings=settings(max_examples=n, stateful_step_count=60, deadline=None, report_multiple_bugs=False, database=None),
)
print("ok", n)
