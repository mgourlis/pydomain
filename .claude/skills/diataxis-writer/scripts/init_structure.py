#!/usr/bin/env python3
"""Initialize a Diataxis documentation folder structure for a project.

Creates the four-quadrant directory layout under a target docs directory
and generates an introduction.md based on project analysis.

Usage:
    python init_structure.py [--target-dir docs/diataxis] [--project-dir .]
"""

import argparse
import json
from pathlib import Path

SECTION_INDEXES = {
    "getting-started": "Tutorials — guided, hands-on paths through the project",
    "how-to": "How-to guides — solve specific problems with copy-paste-ready steps",
    "concepts": "Concepts — in-depth explanations of how and why things work",
    "api-reference": "API Reference — technical descriptions of the machinery",
}

SUBDIRS = [
    "getting-started",
    "how-to",
    "concepts",
    "api-reference",
]

INDEX_TEMPLATE = """# {title}

{description}

| Page | Description |
|------|-------------|
{rows}
"""

INTRODUCTION_TEMPLATE = """# {project_title}

{description}

## Design philosophy

{philosophy}

## How the documentation is organized

This documentation follows the [Diataxis](https://diataxis.fr/) framework,
organized into four sections to serve different needs:

| Section | Purpose | When to read |
|---------|---------|--------------|
| **[Getting Started](getting-started/_index.md)** | Learning-oriented tutorials | You're new here — follow along step by step |
| **[How-To Guides](how-to/_index.md)** | Task-oriented recipes | You know what you want to achieve and need directions |
| **[Concepts](concepts/_index.md)** | Understanding-oriented explanations | You want to understand why things work the way they do |
| **[API Reference](api-reference/_index.md)** | Information-oriented technical descriptions | You need to look up a specific class, function, or option |

## Quick links

{quick_links}
"""


def detect_project_info(project_dir: Path) -> dict:
    """Detect project metadata from common project files."""
    info = {
        "title": project_dir.resolve().name,
        "description": "",
        "philosophy": "",
    }

    # Try pyproject.toml
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("name"):
                    name = line.split("=", 1)[1].strip().strip('"').strip("'")
                    info["title"] = name
                if line.startswith("description"):
                    desc = line.split("=", 1)[1].strip().strip('"').strip("'")
                    info["description"] = desc
        except Exception:
            pass

    # Try package.json
    package_json = project_dir / "package.json"
    if package_json.exists() and not info["description"]:
        try:
            data = json.loads(package_json.read_text())
            info["title"] = data.get("name", info["title"])
            info["description"] = data.get("description", "")
        except Exception:
            pass

    # Try README.md for description and philosophy
    for readme_name in ("README.md", "readme.md", "Readme.md"):
        readme = project_dir / readme_name
        if readme.exists():
            readme_text = readme.read_text()
            if not info["description"]:
                info["description"] = extract_description(readme_text)
            if not info["philosophy"]:
                info["philosophy"] = extract_philosophy(readme_text)
            break

    # Fallback title
    if not info["title"] or info["title"] == project_dir.resolve().name:
        info["title"] = (
            project_dir.resolve().name.replace("-", " ").replace("_", " ").title()
        )

    # Fallback description
    if not info["description"]:
        info["description"] = f"{info['title']} — project documentation."

    if not info["philosophy"]:
        info["philosophy"] = (
            "This project aims to be simple, composable, and well-documented. "
            "It favors explicit over implicit, composition over inheritance, "
            "and correctness over convenience."
        )

    return info


def extract_description(readme: str) -> str:
    """Extract a one-line description from a README."""
    lines = readme.strip().splitlines()
    for line in lines:
        line = line.strip()
        # Skip headings, badges, and empty lines
        if line.startswith("#") or line.startswith("[!") or not line:
            continue
        if len(line) > 20:
            return line.strip("*_ ")
    return ""


def extract_philosophy(readme: str) -> str:
    """Extract design philosophy from a README."""
    # Look for sections about philosophy, principles, design
    in_section = False
    collected = []
    section_headers = ("philosophy", "principles", "design", "goals", "about", "why")

    for line in readme.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("##") and any(h in stripped for h in section_headers):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("##") or stripped.startswith("#"):
                break
            if stripped:
                collected.append(line.strip())

    if collected:
        return " ".join(collected[:3])
    return ""


def detect_source_structure(project_dir: Path) -> list[str]:
    """Discover top-level source modules."""
    modules = []

    # Python: src/package/module or package/module
    for src_candidate in (
        "src",
        "lib",
        "source",
        "app",
        project_dir.name.replace("-", "_"),
    ):
        src_path = project_dir / src_candidate
        if src_path.is_dir():
            modules.extend(
                d.name
                for d in sorted(src_path.iterdir())
                if d.is_dir()
                and not d.name.startswith("_")
                and not d.name.startswith(".")
            )

    # Check directly in project dir for Python packages (contain __init__.py)
    if not modules:
        for d in sorted(project_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                if (d / "__init__.py").exists():
                    modules.append(d.name)

    # Generic: any non-hidden, non-docs, non-test directory with files
    if not modules:
        skip = {
            "docs",
            "tests",
            "test",
            "node_modules",
            ".git",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            "coverage",
            ".mypy_cache",
        }
        modules = [
            d.name
            for d in sorted(project_dir.iterdir())
            if d.is_dir()
            and d.name not in skip
            and not d.name.startswith(".")
            and not d.name.startswith("_")
            and any(
                f.suffix in (".py", ".ts", ".js", ".rs", ".go") for f in d.iterdir()
            )
        ]

    return modules


def create_index(target_dir: Path, section: str) -> None:
    """Create a _index.md for a section."""
    index_path = target_dir / section / "_index.md"
    if index_path.exists():
        return

    title = section.replace("-", " ").title()
    description = SECTION_INDEXES.get(section, f"{title} section.")

    content = INDEX_TEMPLATE.format(
        title=title,
        description=description,
        rows="",
    )
    index_path.write_text(content)


def create_introduction(target_dir: Path, project_dir: Path) -> None:
    """Create introduction.md from project analysis."""
    intro_path = target_dir / "introduction.md"
    if intro_path.exists():
        return

    info = detect_project_info(project_dir)
    modules = detect_source_structure(project_dir)

    quick_links = ""
    if modules:
        quick_links = "### Module documentation\n\n"
        quick_links += "\n".join(
            f"- **{m}** — [Concepts](concepts/{m}/_index.md) · [How-to](how-to/{m}/_index.md)"
            for m in modules
        )

    content = INTRODUCTION_TEMPLATE.format(
        project_title=info["title"],
        description=info["description"],
        philosophy=info["philosophy"],
        quick_links=quick_links or "- [Getting Started](getting-started/_index.md)",
    )
    intro_path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize a Diataxis documentation folder structure."
    )
    parser.add_argument(
        "--target-dir",
        default="docs",
        help="Target directory for the Diataxis structure (default: docs)",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project root directory to analyze (default: .)",
    )
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    project_dir = Path(args.project_dir).resolve()

    # Create directory structure
    dirs_created = []
    for subdir in SUBDIRS:
        d = target_dir / subdir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            dirs_created.append(str(d))

    if dirs_created:
        print(f"Created {len(dirs_created)} directories:")
        for d in dirs_created:
            print(f"  {d}")
    else:
        print("All directories already exist (idempotent).")

    # Create _index.md files
    for section in SUBDIRS:
        create_index(target_dir, section)

    # Create introduction.md
    intro_path = target_dir / "introduction.md"
    existed = intro_path.exists()
    create_introduction(target_dir, project_dir)
    if existed:
        print("Skipped introduction.md (already exists)")
    else:
        print("Created introduction.md")

    print("\nDiataxis structure ready.")


if __name__ == "__main__":
    main()
