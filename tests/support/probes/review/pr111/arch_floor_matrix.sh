#!/usr/bin/env bash
# Run the pydomain suite at given pydantic / uuid-utils versions and Python (synthetic; no repo writes).
# usage: arch_floor_matrix.sh <worktree> <python> <pydantic> <uuid-utils>
W=$1; PY=$2; PD=$3; UU=$4
cd "$W" && PYTHONPATH="$W/src" uv run --isolated --no-project --python "$PY" \
  --with "pydantic==$PD" --with "uuid-utils==$UU" --with pytest --with hypothesis --with anyio \
  --with pytest-cov --with pytest-archon python -m pytest -q -p no:cacheprovider -o addopts="" tests | tail -1
