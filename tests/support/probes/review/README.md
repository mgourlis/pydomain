# Wave 1 review probes

Scripts that reviewers and the coordinator wrote to break the Wave 1 fixes. They are kept as written, are not
collected by pytest, and are excluded from ruff and mypy, like the audit probes one level up.

DCE-127 turned most review scenarios into committed tests and deleted their scripts. It skipped some without a
test, for a stated reason: the trio backend (not a dev dependency), timing-only measurements, the
dependency-floor matrix (the CI `floor` job runs it), and probes of behaviour that no longer exists. DCE-128 did the same for the PR #102, #105 and #109 scripts
(B-88, B-89, B-90). The scripts below stay because they reproduce rows that are still open, or belong to an open issue (decision-log P-28).

Run one from the repository root with `PYTHONPATH=src:. python tests/support/probes/review/<path>`.

| Path | Scenario | Open row → owner |
|---|---|---|
| `pr117/one_key_lock_k3.py` | Locks on one aggregate key do not serialize the commits: the lock is released before the commit | CQRS-K3 → W2-1 |
