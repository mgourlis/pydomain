# Wave 1 review probes

Scripts that reviewers and the coordinator wrote to break the Wave 1 fixes. They are kept as written, are not
collected by pytest, and are excluded from ruff and mypy, like the audit probes one level up. DCE-127 turns
each scenario into a committed test and then deletes this folder.

Run one from the repository root with `PYTHONPATH=src python tests/support/probes/review/<path>`. Some import
`tests/support`, and some were written against a PR branch, so they may need that branch's `src`.

| Path | PR / issue | Scenario | Layer for the committed test |
|---|---|---|---|
| `pr102/break.py`, `break2.py`, `break3.py`, `p.py` | #102 DCE-117 | Upcast edge cases: string, bool, float and non-ASCII stored versions; `default_factory` versions; upcasters returning non-dicts or overriding `upcast()`; newer stored versions; round trips | Regression examples; a Hypothesis property over stored-version values |
| `pr103/p1.py` to `p4.py`, `ids.py`, `stamp.py`, `dce125_probe*.py` | #103 DCE-125 | Copy matrix (shallow, deep, pickle, `copy.replace`, `model_construct`); id types (`NewType`, nested aliases and unions, `Annotated`, `Literal`, `Any`); `stamp()` validators; composite overrides calling `super()`; deep dumps | Regression examples; Hypothesis specification laws with composite subclasses |
| `pr105/break.py`, `drain_paths.py`, `probe_shared_repo.py` | #105 DCE-116 | Rollback and committed-exit drains, raising `pull_events()`, raising storage rollback, cancellation; concurrent units of work on one shared repository (the documented limit, B-19) | Failure injection; a test that pins the B-19 limit |
| `pr107/review_probe_107.py` | #107 DCE-118 | Content sentinel through nested models, dict keys, custom validator messages, `model_validator`, exception text, `ExceptionGroup`, chained causes, log records' `exc_info` and args | Content sentinel tests in `tests/test_sensitive_payloads.py` |
| `pr109/break.py`, `gc_shield.py` | #109 DCE-121 | Start and shutdown: failing, hanging and cancelled stops and starts; shielded cleanup; payload text in exception notes | Failure injection; cancellation |
| `pr109/break2.py`, `r2_anyio_probe.py` | #109 DCE-121 (re-review) | B-45 double cancel; caller's list mutated during start; done-then-cancel race; anyio `move_on_after`/`fail_after` around start | Failure injection; cancellation (converted in PR #109) |
| `pr109/doc_exec.py`, `doc_uncalled.py`, `doc_fixtures_harness.py` | #109 DCE-121 (re-review) | Which asserts on runnable pages really run; `test_*`/`main` defined but never called; calling them with pytest fixtures resolved | Docs-runner guard (DCE-126, P-19) |
| `decisions/probe_commit_twice.py` | DCE-116 (B-1) | A second `commit()` duplicating outbox writes | Regression example (now raises) |
| `decisions/probe_neg_offset.py` | DCE-124 (B-21) | Negative read offsets slicing from the end | Contract case (now raises) |
| `decisions/lock_probe.py` | DCE-116 (B-19) | `AggregateLockingBehavior` does not protect a shared repository's buffer | Pins the documented limit |
| `decisions/anyio_probe.py` | DCE-112 | anyio reuses its asyncio runner for `[trio]` cases while a session-scoped async fixture is alive | Contract-suite backend test, if still relevant |
