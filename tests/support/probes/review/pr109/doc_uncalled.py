import sys, inspect
from pathlib import Path
from tests.docs.doc_examples import run_page, runnable_pages
root = Path("docs/diataxis").resolve()
for path in runnable_pages(root):
    called=set()
    def tr(frame, event, arg):
        if event=="call" and frame.f_code.co_filename==str(path): called.add(frame.f_code.co_firstlineno)
        return None
    sys.settrace(tr)
    try: ns=run_page(path)
    finally: sys.settrace(None)
    missing=[]
    def visit(obj, label):
        if inspect.isfunction(obj) and obj.__code__.co_filename==str(path):
            code=obj.__code__
            if code.co_firstlineno not in called: missing.append(f"{label}@{code.co_firstlineno}")
    for name,obj in ns.items():
        if inspect.isfunction(obj) and (name.startswith("test_") or name=="main" or name.startswith("build")): visit(obj,name)
        elif inspect.isclass(obj) and getattr(obj,"__module__",None)==ns.get("__name__"):
            for m,f in vars(obj).items():
                f=getattr(f,"__wrapped__",f)
                if inspect.isfunction(f) and (m.startswith("test_")): visit(f,f"{name}.{m}")
                elif hasattr(f, "_get_wrapped_function"):
                    try: visit(f._get_wrapped_function(), f"{name}.{m}[fixture]")
                    except Exception: pass
    if missing: print(path.relative_to(root), missing)
