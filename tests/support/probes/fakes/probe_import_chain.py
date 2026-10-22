"""Print the import chain at the moment pydomain.es is first loaded, per target."""

import subprocess
import sys

CHILD = r"""
import sys, traceback
class Hook:
    def find_spec(self, name, path=None, target=None):
        if name == "pydomain.es":
            frames = [f for f in traceback.extract_stack() if "pydomain" in f.filename and "importlib" not in f.filename]
            print(" <- ".join(f"{f.filename.split('src/')[-1]}:{f.lineno}" for f in reversed(frames)))
        return None
sys.meta_path.insert(0, Hook())
import {target}
"""
for t in ["pydomain.ddd", "pydomain.cqrs", "pydomain.infrastructure.message_bus"]:
    out = subprocess.run([sys.executable, "-I", "-c", CHILD.replace("{target}", t)],
                         capture_output=True, text=True, check=True).stdout.strip()
    print(t, "=>", out)
