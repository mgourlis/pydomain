"""§4.7: which imports load pydomain.es / cqrs / third-party, each in a fresh subprocess."""

import json
import subprocess
import sys

CHILD = r"""
import sys, json
before = set(sys.modules)
import {target}
after = set(sys.modules) - before
top = sorted({{m.split('.')[0] for m in after}})
print(json.dumps({{
  "es": any(m == "pydomain.es" or m.startswith("pydomain.es.") for m in after),
  "cqrs": any(m == "pydomain.cqrs" or m.startswith("pydomain.cqrs.") for m in after),
  "infra": any(m.startswith("pydomain.infrastructure") for m in after),
  "testing": any(m.startswith("pydomain.testing") for m in after),
  "pydomain_root_ran": "pydomain" in after,
  "third_party_top": [t for t in top if t not in sys.stdlib_module_names and t != "pydomain"],
}}))
"""

TARGETS = [
    "pydomain.ddd",
    "pydomain.ddd.entity",
    "pydomain.cqrs",
    "pydomain.infrastructure.message_bus",
    "pydomain.testing",
]

for t in TARGETS:
    out = subprocess.run(
        [sys.executable, "-I", "-c", CHILD.format(target=t)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    print(t, json.loads(out))

# Locate the chain: who first imports pydomain.es when importing pydomain.ddd
trace = subprocess.run(
    [sys.executable, "-I", "-X", "importtime", "-c", "import pydomain.ddd"],
    capture_output=True, text=True, check=True,
).stderr.splitlines()
first_es = next(i for i, l in enumerate(trace) if l.rstrip().endswith("pydomain.es") or "pydomain.es." in l)
print("first es line in importtime trace:", trace[first_es].strip())
cum = [l for l in trace if l.rstrip().endswith("| pydomain")]
print("cumulative pydomain root:", cum[-1].strip() if cum else None)
