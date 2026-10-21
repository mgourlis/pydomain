"""Under the CI profile (derandomized, 200 examples, as committed), does the machine catch B-71 / B-79 regressions in the fake?"""
import sys

from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

import probe_stateful_fake_mutants_lib as lib  # noqa: F401  (patches the fake per argv[1])
from tests.infrastructure.test_outbox_relay_stateful import OutboxRelayMachine

try:
    run_state_machine_as_test(OutboxRelayMachine, settings=settings(max_examples=200, stateful_step_count=30, derandomize=True, database=None, deadline=None, report_multiple_bugs=False))
    print(sys.argv[1], "NOT caught under CI profile")
except AssertionError:
    print(sys.argv[1], "caught under CI profile")
