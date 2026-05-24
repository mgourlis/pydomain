#!/usr/bin/env python3
"""Lint the LLM wiki for broken links, missing sections, and catalog consistency."""

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-dir", default="docs/llm-wiki")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    wiki = Path(args.wiki_dir)
    errors = 0
    warnings = 0

    # 1. Check each wiki page (excluding meta files)
    for f in wiki.glob("*.md"):
        if f.name in ("_index.md", "catalog.md", "log.md"):
            continue
        text = f.read_text(encoding="utf-8")

        if "## TL;DR" not in text:
            print(f"WARNING: {f.name} missing TL;DR")
            warnings += 1

        # internal wikilinks
        for m in re.finditer(r"\[\[([^\]]+)\]\]", text):
            target = m.group(1)
            # allow analysis/ prefix
            target_file = wiki / f"{target}.md"
            if not target_file.exists():
                print(f"WARNING: {f.name} links to non-existent [[{target}]]")
                warnings += 1

        # source link (relative ../)
        source_match = re.search(r"\[.*?\]\((\.\.\/.*?)\)", text)
        if source_match:
            source_path = Path(source_match.group(1))
            if not (wiki.parent / source_path).resolve().exists():
                print(f"WARNING: {f.name} source link broken: {source_path}")
                warnings += 1

    # 2. Catalog consistency
    catalog_path = wiki / "catalog.md"
    if catalog_path.exists():
        catalog_text = catalog_path.read_text(encoding="utf-8")
        listed_pages = set()
        for m in re.finditer(r"\[\[([^\]]+)\]\]", catalog_text):
            listed_pages.add(m.group(1))
        actual_pages = {
            f.stem
            for f in wiki.glob("*.md")
            if f.name not in ("_index.md", "catalog.md", "log.md")
        }
        missing_in_catalog = actual_pages - listed_pages
        extra_in_catalog = listed_pages - actual_pages
        if missing_in_catalog:
            print(
                "WARNING: pages missing in catalog.md: "
                "{', '.join(sorted(missing_in_catalog))}"
            )
            warnings += len(missing_in_catalog)
        if extra_in_catalog:
            print(
                "WARNING: catalog.md lists non-existent pages: "
                "{', '.join(sorted(extra_in_catalog))}"
            )
            warnings += len(extra_in_catalog)
    else:
        print("WARNING: catalog.md not found")
        warnings += 1

    # 3. Append log entry
    log_path = wiki / "log.md"
    if warnings > 0 or errors > 0:
        entry = (
            f"## [{datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}] lint\n"
            f"- {errors} errors, {warnings} warnings.\n"
        )
        with open(log_path, "a") as f:
            f.write(entry)
    else:
        entry = (
            f"## [{datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}] lint\n- All clear.\n"
        )
        with open(log_path, "a") as f:
            f.write(entry)

    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
