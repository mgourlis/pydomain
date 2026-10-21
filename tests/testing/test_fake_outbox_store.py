"""FakeOutboxStore behaviour beyond the OutboxStore contract (ADR-074)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pydomain.cqrs.outbox import OutboxEntry
from pydomain.testing import FakeOutboxStore

_STALE = "DCE-122 FAKE-05: FakeOutboxStore has no claim_timeout"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _entry(message_id: str) -> OutboxEntry:
    return OutboxEntry(
        message_id=message_id,
        topic="invoices.issued",
        payload={"invoice_ref": "synthetic"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestFakeOutboxStoreClaimTimeout:
    @pytest.mark.xfail(strict=True, raises=TypeError, reason=_STALE)
    @pytest.mark.parametrize("seconds", [0, -1])
    def test_non_positive_claim_timeout_raises_value_error(self, seconds: int) -> None:
        with pytest.raises(ValueError, match="claim_timeout"):
            FakeOutboxStore(claim_timeout=timedelta(seconds=seconds))

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_STALE)
    @pytest.mark.anyio
    async def test_default_claim_lasts_five_minutes(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(minutes=5) - timedelta(microseconds=1)
        assert await store.fetch_unpublished() == []

        clock.now += timedelta(microseconds=1)
        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]


class TestFakeOutboxStoreClaims:
    @pytest.mark.xfail(strict=True, raises=TypeError, reason=_STALE)
    @pytest.mark.anyio
    async def test_claim_is_held_until_the_claim_timeout(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(seconds=29)

        assert await store.fetch_unpublished() == []

    @pytest.mark.xfail(strict=True, raises=TypeError, reason=_STALE)
    @pytest.mark.anyio
    async def test_stale_claim_is_fetched_again(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(seconds=30)

        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]

    @pytest.mark.xfail(strict=True, raises=TypeError, reason=_STALE)
    @pytest.mark.anyio
    async def test_dead_lettered_entry_stays_hidden_after_its_claim_expires(
        self,
    ) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()
        await store.mark_dead_lettered("m1", error="gave up")

        clock.now += timedelta(minutes=5)

        assert await store.fetch_unpublished() == []
