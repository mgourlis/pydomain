#!/usr/bin/env python3
"""Lint a Diataxis documentation structure for common issues.

Checks:
- Broken internal relative links
- Orphaned pages (not referenced in any _index.md or other page)
- Empty directories (sections with no content pages)
- Mode-discipline heuristics (teaching language in how-tos, etc.)
- Missing _index.md files
- Inconsistent naming (pages not matching conventions)

Usage:
    python lint_docs.py --docs-dir docs
    python lint_docs.py --docs-dir docs --strict  # exit non-zero on warnings too
"""

import argparse
import re
import sys
from pathlib import Path

# Heuristic patterns for mode-discipline violations
# These are NOT definitive but serve as useful flags for human review
MODE_VIOLATION_PATTERNS = {
    "how-to": [
        (
            r"you will learn",
            "Teaching language in how-to — use 'This guide shows you how to...'",
        ),
        (
            r"in this tutorial",
            "Tutorial language in how-to — use 'This guide shows you how to...'",
        ),
        (
            r"let's understand",
            "Teaching language in how-to — link to a concept page instead",
        ),
        (
            r"first,.*understand",
            "Teaching in how-to — state the action, not the lesson",
        ),
    ],
    "tutorial": [
        (
            r"you will learn",
            "Avoid 'you will learn' — show the destination as an experience",
        ),
        (
            r"this guide shows you how",
            "How-to language in tutorial — use 'In this tutorial, we will...'",
        ),
    ],
    "concepts": [
        (
            r"^(#+)\s*How to",
            "How-to title in concept page — use a noun phrase or 'Understanding...'",
        ),
    ],
}

# Pages that are expected to have non-standard content
SKIP_MODE_CHECK_FILES = {"_index.md", "introduction.md"}

LinkRef = tuple[str, str]  # (source_file, target_ref)


def find_markdown_files(docs_dir: Path) -> list[Path]:
    """Find all Markdown files in the docs directory."""
    return sorted(docs_dir.rglob("*.md"))


def build_page_index(md_files: list[Path], docs_dir: Path) -> dict[str, Path]:
    """Build a map of page slugs/names to their file paths.

    Handles both relative paths (GitHub) and page names (Obsidian).
    """
    index: dict[str, Path] = {}

    for f in md_files:
        rel = f.relative_to(docs_dir)
        # Register by relative path (without .md)
        index[str(rel).replace(".md", "")] = f
        index[str(rel)] = f
        # Register by stem only (for Obsidian-style references)
        index[f.stem] = f

    return index


def extract_links(content: str, source_file: Path) -> list[LinkRef]:
    """Extract all internal Markdown links from content.

    Returns list of (source_file, target_reference) tuples.
    """
    links: list[LinkRef] = []
    source = str(source_file)

    # Standard Markdown links: [text](path)
    for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", content):
        target = match.group(2)
        # Skip external URLs
        if not target.startswith(("http://", "https://", "#", "mailto:")):
            links.append((source, target))

    # Obsidian wikilinks: [[page]] or [[page|text]]
    for match in re.finditer(r"\[\[([^\]|#]+)(?:[#|][^\]]+)?\]\]", content):
        target = match.group(1)
        links.append((source, target))

    return links


def check_broken_links(md_files: list[Path], docs_dir: Path) -> list[str]:
    """Check for broken internal links."""
    page_index = build_page_index(md_files, docs_dir)
    issues: list[str] = []

    for f in md_files:
        content = f.read_text()
        links = extract_links(content, f)

        for source, target in links:
            # Normalize: strip .md extension, handle fragments
            clean_target = target.split("#")[0]
            if clean_target.endswith(".md"):
                clean_target = clean_target[:-3]

            # Try to resolve
            if clean_target not in page_index:
                # Try resolving relative to the source file's directory
                resolved = (f.parent / target).resolve()
                resolved_rel = str(resolved.relative_to(docs_dir)).replace(".md", "")
                if resolved_rel not in page_index and not resolved.exists():
                    issues.append(
                        f"{f.relative_to(docs_dir)}: broken link -> '{target}'"
                    )

    return issues


def check_empty_sections(docs_dir: Path, md_files: list[Path]) -> list[str]:
    """Check for empty sections (directories with only an _index.md)."""
    issues: list[str] = []

    for d in sorted(docs_dir.rglob("*")):
        if not d.is_dir():
            continue
        md_in_dir = [f for f in md_files if f.parent == d]
        non_index = [f for f in md_in_dir if f.name != "_index.md"]

        if not non_index and d.relative_to(docs_dir).parts[0] in {
            "getting-started",
            "how-to",
            "concepts",
            "api-reference",
        }:
            # Only flag if it's inside a known Diataxis section
            depth = len(d.relative_to(docs_dir).parts)
            if depth >= 2:  # e.g., how-to/cqrs/ — depth 2+
                issues.append(
                    f"{d.relative_to(docs_dir)}/: empty section (no content pages)"
                )

    return issues


def check_missing_indexes(docs_dir: Path) -> list[str]:
    """Check for directories missing _index.md files."""
    issues: list[str] = []

    for d in sorted(docs_dir.rglob("*")):
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        # Only check inside known Diataxis sections
        if d.relative_to(docs_dir).parts[0] not in {
            "getting-started",
            "how-to",
            "concepts",
            "api-reference",
        }:
            continue
        index_file = d / "_index.md"
        if not index_file.exists():
            issues.append(f"{d.relative_to(docs_dir)}/: missing _index.md")

    return issues


def check_orphaned_pages(md_files: list[Path], docs_dir: Path) -> list[str]:
    """Check for pages not referenced in any _index.md."""
    issues: list[str] = []

    # Collect all referenced pages from index files
    referenced: set[str] = set()
    for f in md_files:
        if f.name == "_index.md":
            content = f.read_text()
            for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", content):
                target = match.group(2)
                if not target.startswith(("http://", "https://", "#", "mailto:")):
                    referenced.add(target.replace(".md", ""))

    for f in md_files:
        if f.name in ("_index.md", "introduction.md"):
            continue
        rel = str(f.relative_to(docs_dir)).replace(".md", "")
        stem = f.stem
        if rel not in referenced and stem not in referenced:
            # Also check parent-directory references
            parent_ref = f"{f.parent.name}/{f.name}".replace(".md", "")
            if parent_ref not in referenced:
                issues.append(
                    f"{f.relative_to(docs_dir)}: orphaned (not linked from any _index.md)"
                )

    return issues


def check_mode_discipline(md_files: list[Path], docs_dir: Path) -> list[str]:
    """Check for mode-discipline violations using heuristic patterns."""
    issues: list[str] = []

    for f in md_files:
        if f.name in SKIP_MODE_CHECK_FILES:
            continue

        rel = str(f.relative_to(docs_dir))
        content = f.read_text()

        # Determine which mode this file likely belongs to
        detected_mode = None
        for mode in MODE_VIOLATION_PATTERNS:
            mode_dir = mode.replace("-", "")
            if mode in rel.lower() or mode_dir in rel.lower():
                detected_mode = mode
                break

        if not detected_mode:
            continue

        patterns = MODE_VIOLATION_PATTERNS.get(detected_mode, [])
        for pattern, message in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                issues.append(f"{rel}: {message}")

    return issues


def check_inconsistent_naming(md_files: list[Path], docs_dir: Path) -> list[str]:
    """Check for naming inconsistencies."""
    issues: list[str] = []

    for f in md_files:
        if f.name in SKIP_MODE_CHECK_FILES:
            continue
        rel = str(f.relative_to(docs_dir))

        # How-to pages should start with a verb or "how-to" pattern
        if "how-to" in rel.lower() and not f.name.startswith("_"):
            if not any(
                f.stem.lower().startswith(prefix)
                for prefix in (
                    "define-",
                    "create-",
                    "implement-",
                    "configure-",
                    "handle-",
                    "use-",
                    "add-",
                    "connect-",
                    "track-",
                    "register-",
                    "bootstrap-",
                    "publish-",
                )
            ):
                issues.append(
                    f"{rel}: how-to filename should start with an action verb (define-, create-, implement-, etc.)"
                )

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lint a Diataxis documentation structure."
    )
    parser.add_argument(
        "--docs-dir",
        default="docs",
        help="Path to the Diataxis documentation directory (default: docs)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings too (not just errors)",
    )
    parser.add_argument(
        "--skip-mode-check",
        action="store_true",
        help="Skip mode-discipline heuristic checks",
    )
    parser.add_argument(
        "--skip-orphan-check",
        action="store_true",
        help="Skip orphaned page checks",
    )
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir).resolve()
    if not docs_dir.is_dir():
        print(f"Error: {docs_dir} is not a directory")
        sys.exit(1)

    md_files = find_markdown_files(docs_dir)
    if not md_files:
        print("No Markdown files found.")
        sys.exit(0)

    all_issues: dict[str, list[str]] = {}
    errors = 0
    warnings = 0

    # Run checks
    checks = [
        ("Broken links", check_broken_links(md_files, docs_dir), True),
        ("Empty sections", check_empty_sections(docs_dir, md_files), False),
        ("Missing _index.md", check_missing_indexes(docs_dir), False),
    ]

    if not args.skip_orphan_check:
        checks.append(
            ("Orphaned pages", check_orphaned_pages(md_files, docs_dir), False)
        )

    if not args.skip_mode_check:
        checks.append(
            ("Mode discipline", check_mode_discipline(md_files, docs_dir), False)
        )

    checks.append(
        ("Naming conventions", check_inconsistent_naming(md_files, docs_dir), False)
    )

    for check_name, issues, is_error in checks:
        if issues:
            all_issues[check_name] = issues
            if is_error:
                errors += len(issues)
            else:
                warnings += len(issues)

    # Report
    if not all_issues:
        print("Documentation lint: all checks passed.")
        sys.exit(0)

    for check_name, issues in all_issues.items():
        severity = "ERROR" if check_name == "Broken links" else "WARNING"
        print(
            f"\n{check_name} ({severity} — {len(issues)} issue{'s' if len(issues) != 1 else ''}):"
        )
        for issue in issues:
            print(f"  - {issue}")

    print(f"\nSummary: {errors} error(s), {warnings} warning(s)")

    exit_code = 1 if errors > 0 or (args.strict and warnings > 0) else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
