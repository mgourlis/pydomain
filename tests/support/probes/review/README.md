# Wave 1 review probes

Scripts that reviewers and the coordinator wrote to break the Wave 1 fixes. They are kept as written, are not
collected by pytest, and are excluded from ruff and mypy, like the audit probes one level up.

DCE-127 turned every other review probe into a committed test and deleted it. The three left belong to the
docs-runner guard (DCE-126, P-19) and go when that issue has its tests.

Run one from the repository root with `PYTHONPATH=src:. python tests/support/probes/review/<path>`.

| Path | PR / issue | Scenario | Layer for the committed test |
|---|---|---|---|
| `pr109/doc_exec.py`, `doc_uncalled.py`, `doc_fixtures_harness.py` | #109 DCE-121 (re-review) | Which asserts on runnable pages really run; `test_*`/`main` defined but never called; calling them with pytest fixtures resolved | Docs-runner guard (DCE-126, P-19) |
