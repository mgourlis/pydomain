#!/usr/bin/env python3
"""Initialize llm-wiki-config.yaml for a project."""

import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "references" / "config-template.yaml"


def detect_structure(project_root):
    """Return a dict of detected settings based on the project's docs folder."""
    docs = project_root / "docs"
    settings = {
        "source_dirs": ["docs"],
        "diataxis_layout": False,
        "adr_dir": "",
    }
    if not docs.is_dir():
        return settings

    # Diataxis detection: check for the standard four quadrant folders
    quadrants = {"getting-started", "how-to", "concepts", "api-reference"}
    if all((docs / q).is_dir() for q in quadrants):
        settings["diataxis_layout"] = True
        # Also include docs/adr if it exists, but only as separate source dir
        adr = docs / "adr"
        if adr.is_dir():
            settings["adr_dir"] = str(adr.relative_to(project_root))

    return settings


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--project-dir":
        project_root = Path(sys.argv[2])
    else:
        project_root = Path.cwd()

    config_dest = project_root / "llm-wiki-config.yaml"
    if config_dest.exists():
        print(
            f"Config already exists at {config_dest}. Delete it first to re-initialize."
        )
        sys.exit(1)

    # Copy template
    shutil.copy(TEMPLATE, config_dest)

    # Update with detected values
    detected = detect_structure(project_root)
    # Simple string replacement (or we could use yaml for a proper update)
    content = config_dest.read_text()
    content = content.replace(
        "source_dirs:\n  - docs", f"source_dirs:\n  - {detected['source_dirs'][0]}"
    )
    content = content.replace(
        "diataxis_layout: false",
        f"diataxis_layout: {str(detected['diataxis_layout']).lower()}",
    )
    content = content.replace('adr_dir: ""', f'adr_dir: "{detected["adr_dir"]}"')
    config_dest.write_text(content)

    print(f"Config created: {config_dest}")
    print(
        f"Detected diataxis_layout: {detected['diataxis_layout']}, "
        "adr_dir: {detected['adr_dir']}"
    )


if __name__ == "__main__":
    main()
