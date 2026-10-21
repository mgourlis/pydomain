"""Probe: run each unmarked page as if marked runnable; report the first failing block.

usage: python arch_try_runnable.py <worktree> <page>...
"""
import sys, tempfile, traceback
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "src"))
from tests.docs.doc_examples import run_page
for rel in sys.argv[2:]:
    src = root / rel
    text = src.read_text()
    lines = text.splitlines(keepends=True)
    lines.insert(1, "\n<!-- docs-test: runnable -->\n")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, dir=src.parent) as f:
        f.write("".join(lines)); tmp = Path(f.name)
    try:
        ns = run_page(tmp)
        print("RUNS  ", rel, "defines:", sorted(k for k in ns if k.startswith(("test_", "main", "Test")))[:8])
    except BaseException as e:
        print("FAILS ", rel, "->", str(e).splitlines()[0][:220].replace(str(tmp.name), "PAGE"), "|", type(getattr(e, "__cause__", None) or e).__name__, str(getattr(e, "__cause__", "") or "")[:150])
    finally:
        tmp.unlink()
