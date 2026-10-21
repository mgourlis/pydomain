# Wave 1 review probes

Scripts that reviewers and the coordinator wrote to break the Wave 1 fixes. They are kept as written, are not
collected by pytest, and are excluded from ruff and mypy, like the audit probes one level up.

DCE-127 turned most review scenarios into committed tests and deleted their scripts. It skipped some without a
test, for a stated reason: the trio backend (not a dev dependency), timing-only measurements, the
dependency-floor matrix (the CI `floor` job runs it), and probes of behaviour that no longer exists. The scripts
below stay because they reproduce rows that are still open, or belong to an open issue (decision-log P-28).

Run one from the repository root with `PYTHONPATH=src:. python tests/support/probes/review/<path>`.

| Path | Scenario | Open row → owner |
|---|---|---|
| `pr102/break.py`, `break2.py`, `break3.py` | Stored versions that conflict with the payload's `event_version`, and a digit string too long for `int()` | B-89 → DCE-128 |
| `pr105/break.py` (case 2), `drain_paths.py` (case p4) | A cancelled dispatch whose rollback raises; `_rollback()` and `pull_events()` both raising | B-88 → DCE-128 |
| `pr109/shutdown_repeated_note.py` | `shutdown()` adds a note the exception already carries (case D2 of the PR #109 probe `break.py`) | B-90 → DCE-128 |
| `pr117/one_key_lock_k3.py` | Locks on one aggregate key do not serialize the commits: the lock is released before the commit | CQRS-K3 → W2-1 |
| `pr109/doc_exec.py`, `doc_uncalled.py`, `doc_fixtures_harness.py` | Which asserts on runnable pages really run; `test_*`/`main` defined but never called; calling them with pytest fixtures resolved | P-19 → DCE-126 |
