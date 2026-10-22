# Wave 1 review probes

Scripts that reviewers and the coordinator wrote to break the Wave 1 fixes. They are kept as written, are not
collected by pytest, and are excluded from ruff and mypy, like the audit probes one level up.

DCE-127 turned most review scenarios into committed tests and deleted their scripts. It skipped some without a
test, for a stated reason: the trio backend (not a dev dependency), timing-only measurements, the
dependency-floor matrix (the CI `floor` job runs it), and probes of behaviour that no longer exists. DCE-128 did the same for the PR #102, #105 and #109 scripts
(B-88, B-89, B-90). A script stays only while it reproduces a row that is still open, or belongs to an open issue (decision-log P-28).

No script is left. The last one, `pr117/one_key_lock_k3.py` (locks on one aggregate key did not serialize the
commits), was deleted by DCE-143, which fixed CQRS-K3: under the fix it deadlocks by design, and
`TestCommitInTerminalStep` in `tests/cqrs/test_bus.py` replaces it.

Run a new one from the repository root with `PYTHONPATH=src:. python tests/support/probes/review/<path>`.
