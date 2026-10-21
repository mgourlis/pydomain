import sys, subprocess, re
from pathlib import Path
from tests.docs.doc_examples import run_page, parse_page
pages = sorted(subprocess.run(["git","diff","--name-only","e133fd0","--","docs/diataxis"],capture_output=True,text=True).stdout.split())
for p in pages:
    path = Path(p).resolve()
    blocks = parse_page(path)
    if blocks is None: continue
    hit=set()
    def tr(frame, event, arg):
        if frame.f_code.co_filename == str(path):
            hit.add(frame.f_lineno)
            return tr
        return None
    sys.settrace(tr)
    try: run_page(path)
    finally: sys.settrace(None)
    lines = path.read_text().splitlines()
    unexec_asserts=[]; unexec_dispatch=[]
    for b in blocks:
        if b.kind!="run": continue
        for i,l in enumerate(b.code.splitlines(), start=1):
            s=l.strip()
            if i in hit: continue
            if s.startswith("assert"): unexec_asserts.append(i)
            elif "dispatch(" in s or "await " in s: unexec_dispatch.append(i)
    total_asserts=sum(1 for b in blocks if b.kind=="run" for l in b.code.splitlines() if l.strip().startswith("assert"))
    print(f"{p}: asserts {total_asserts-len(unexec_asserts)}/{total_asserts} executed; unexecuted assert lines {unexec_asserts[:12]}; unexecuted await/dispatch lines {unexec_dispatch[:15]}")
