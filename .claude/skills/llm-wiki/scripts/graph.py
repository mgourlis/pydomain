#!/usr/bin/env python3
"""Generate a Mermaid mindmap from the wiki index."""

import argparse
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-dir", default="docs/llm-wiki")
    args = parser.parse_args()

    wiki = Path(args.wiki_dir)
    lines = ["```mermaid", "mindmap", "  root((Project Wiki))"]

    # Prefer _index.md
    index = wiki / "_index.md"
    if not index.exists():
        # fallback to catalog.md
        index = wiki / "catalog.md"
    if not index.exists():
        print("No index found.")
        return

    text = index.read_text()
    current_section = None
    for line in text.splitlines():
        if line.startswith("## "):
            current_section = line[3:].strip()
            lines.append(f"    {current_section}")
        elif line.startswith("- [["):
            m = re.search(r"\[\[([^\]]+)\]\]", line)
            if m:
                page = m.group(1)
                lines.append(f"      {page}")
    lines.append("```")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
