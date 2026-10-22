# How to Use a Fake Outbox Store

<!-- docs-test: runnable -->

> **Prerequisites:** [Outbox concept](../../concepts/cqrs/integration-events.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You test code that writes to the transactional outbox, or the relay that publishes from it, and you need a store that behaves like a real one (claims, retries, dead letters) without a database.

## Solution

`FakeOutboxStore` is an in-memory `OutboxStore`. It records every append, publication, failure and dead letter, so a test can assert on what happened.

## Steps

### 1. Create the store and some entries

```python
from datetime import UTC, datetime, timedelta

from pydomain.cqrs.outbox import OutboxEntry
from pydomain.testing import FakeOutboxStore


def entry(message_id: str) -> OutboxEntry:
    return OutboxEntry(
        message_id=message_id,
        topic="orders",
        payload={"order_id": "o-1"},
        occurred_at=datetime.now(UTC),
    )


store = FakeOutboxStore()
```

### 2. Read what was written

Assert on the records, not on `fetch_unpublished()`. A fetch *claims* what it returns, so calling it to look inside changes what the relay will see next.

```python
async def test_append_records_entries():
    store = FakeOutboxStore()
    await store.append([entry("m-1"), entry("m-2")])

    assert [e.message_id for e in store.entries] == ["m-1", "m-2"]
    assert store.published == []
```

The records you can read are `entries`, `published` (message ids in publication order), `failures` (`(message_id, error, next_attempt_at)`), `attempts` (failed attempts per message id) and `dead_lettered` (message id to final error). `is_published(id)` and `is_dead_lettered(id)` answer for one id.

### 3. Fetching claims entries

`fetch_unpublished(limit)` returns the unpublished entries whose retry time has passed, and claims them. A second fetch does not return a claimed entry until it is marked, or until `claim_timeout` has passed on the store's clock. This is what keeps two relays from publishing the same row at once. `limit` must be an `int` of at least 1.

```python
async def test_a_fetched_entry_is_claimed():
    store = FakeOutboxStore()
    await store.append([entry("m-1")])

    first = await store.fetch_unpublished()
    second = await store.fetch_unpublished()

    assert [e.message_id for e in first] == ["m-1"]
    assert second == []
```

### 4. Control time with `clock` and `claim_timeout`

A claim lasts `claim_timeout` (five minutes by default; it must be positive). Pass a `clock` so a test moves time instead of waiting, to make a retry due or a claim stale, as when a relay crashed mid-pass:

```python
async def test_a_stale_claim_is_fetched_again():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = FakeOutboxStore(clock=lambda: now, claim_timeout=timedelta(minutes=5))
    await store.append([entry("m-1")])
    await store.fetch_unpublished()

    now += timedelta(minutes=6)  # the first relay never marked it

    assert [e.message_id for e in await store.fetch_unpublished()] == ["m-1"]
```

### 5. Failures, retries and dead letters

`mark_failed()` counts the attempt, sets when the entry is next due and releases the claim. `mark_dead_lettered()` takes the entry out for good. `mark_published()` records the publication.

```python
async def test_failed_entry_waits_for_its_retry_time():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = FakeOutboxStore(clock=lambda: now)
    await store.append([entry("m-1")])
    await store.fetch_unpublished()

    await store.mark_failed("m-1", error="broker down", next_attempt_at=now + timedelta(minutes=1))

    assert store.attempts["m-1"] == 1
    assert await store.fetch_unpublished() == []  # not due yet

    now += timedelta(minutes=2)
    due = await store.fetch_unpublished()
    assert [(e.message_id, e.attempts) for e in due] == [("m-1", 1)]
```

A publication wins over a later failure. If a relay whose claim went stale marks an entry failed after another relay published it, the failure is ignored; the same holds for dead-lettering.

### 6. Inject storage failures

Set `fail_next_append` or `fail_next_fetch` to make the next call raise a `RuntimeError`. The flag clears itself, so one failure is injected.

```python
import pytest


async def test_a_failed_outbox_write_surfaces():
    store = FakeOutboxStore()
    store.fail_next_append = True

    with pytest.raises(RuntimeError):
        await store.append([entry("m-1")])
```

### 7. Check your own adapter

`FakeOutboxStore` follows the same contract as a real store. Run `OutboxStoreContract` against your adapter to check it keeps the same promises: see [Run a Contract Suite](run-contract-suites.md).

<!-- docs-test: check
await test_append_records_entries()
await test_a_fetched_entry_is_claimed()
await test_a_stale_claim_is_fetched_again()
await test_failed_entry_waits_for_its_retry_time()
await test_a_failed_outbox_write_surfaces()
-->

## Expected outcome

Tests that exercise the outbox and the relay deterministically: claims, retries, dead letters and storage failures, with no database and no waiting.

## See also

- [Configure the Outbound Event Gateway](../infrastructure/configure-outbound-event-gateway.md): the relay that reads the outbox
- [Publish Integration Events](../recipes/publish-integration-events.md)
- [Run a Contract Suite Against Your Adapter](run-contract-suites.md)
