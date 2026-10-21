"""Does the two-relay state machine catch a fake that drops B-71 or B-79?

Mode "b71": mark_failed / mark_dead_lettered no longer skip a published entry.
Mode "b79": mark_published no longer clears an earlier dead letter.
"""
import sys

from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

from pydomain.testing import fake_outbox_store as f
from tests.infrastructure.test_outbox_relay_stateful import OutboxRelayMachine

mode = sys.argv[1]
F = f.FakeOutboxStore
if mode == "b71":
    async def mark_dead_lettered(self, message_id, *, error):
        self.dead_lettered[message_id] = error
        self._claimed_until.pop(message_id, None)
        self.attempts[message_id] = self.attempts.get(message_id, 0) + 1
    F.mark_dead_lettered = mark_dead_lettered
elif mode == "b79":
    async def mark_published(self, message_ids):
        for m in message_ids:
            self.published.append(m)
            self._published_ids.add(m)
            self._claimed_until.pop(m, None)
    F.mark_published = mark_published
