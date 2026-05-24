#!/usr/bin/env python3
"""Compile Diataxis/ADR docs into LLM-optimised wiki pages with catalog and log."""

import argparse
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
DEFAULT_SOURCE_DIRS = ["docs"]
DEFAULT_OUTPUT_DIR = "docs/llm-wiki"
DIATAXIS_QUADRANTS = {"getting-started", "how-to", "concepts", "api-reference"}


def load_config(project_root, config_path=None):
    """Load and return a normalized config dict."""
    if config_path:
        cfg_file = Path(config_path)
    else:
        cfg_file = project_root / "llm-wiki-config.yaml"

    defaults = {
        "source_dirs": ["docs"],
        "output_dir": "docs/llm-wiki",
        "diataxis_layout": False,
        "category_labels": {
            "getting-started": "Tutorials",
            "how-to": "How‑To",
            "concepts": "Concepts",
            "api-reference": "API Reference",
            "decisions": "Decisions",
        },
        "adr_dir": None,
        "concept_mapping": {},
    }
    if cfg_file.exists():
        with open(cfg_file) as f:
            user = yaml.safe_load(f) or {}
        # deep merge for category_labels
        for key, val in user.items():
            if key == "category_labels":
                defaults[key].update(val)
            else:
                defaults[key] = val
    return defaults


# ---------------------------------------------------------------------------
# Content extraction helpers
# ---------------------------------------------------------------------------
def extract_sections(text):
    """Return {section_header: body} for ## sections."""
    sections = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if v}


def extract_tldr(sections):
    tldr = sections.get("TL;DR", "").strip()
    if tldr:
        return tldr
    # fallback: first substantial paragraph after H1
    return ""


def extract_rules(sections):
    return sections.get("Rules", "")


def extract_summary(text):
    """Return first paragraph of meaningful text after the title."""
    lines = text.splitlines()
    capture = False
    buf = []
    for line in lines:
        if line.startswith("# "):
            capture = True
            continue
        if capture:
            if line.startswith("## ") or line.strip() == "":
                if buf:
                    break
            elif line.strip():
                buf.append(line.strip())
    return " ".join(buf)


def parse_adr(filename, content):
    """Extract ADR number, title, and first paragraph."""
    m = re.match(r"ADR-(\d+)-(.+)\.md", filename, re.IGNORECASE)
    if not m:
        return None
    number = m.group(1)
    title = m.group(2).replace("-", " ").title()
    # Try to get first paragraph after H1
    lines = content.splitlines()
    in_header = False
    first_para = []
    for line in lines:
        if line.startswith("# "):
            in_header = True
            continue
        if in_header:
            if line.startswith("## ") or line.strip() == "":
                if first_para:
                    break
            elif line.strip():
                first_para.append(line.strip())
    tldr = " ".join(first_para[:3]) if first_para else ""
    return {"number": number, "title": title, "tldr": tldr}


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
def build_wiki(config, root):
    source_dirs = config["source_dirs"]
    output_dir = root / config["output_dir"]
    adr_dir = root / config["adr_dir"] if config["adr_dir"] else None

    # Collect source files
    src_files = []
    for d in source_dirs:
        full = root / d
        if full.is_dir():
            src_files.extend(full.rglob("*.md"))
    # Exclude output dir and any file inside it
    src_files = [f for f in src_files if not str(f).startswith(str(output_dir))]

    output_dir.mkdir(parents=True, exist_ok=True)

    concept_pages = {}  # concept -> (relative source path, page file stem)

    for src in src_files.relative_to(root):
        rel = str(src)
        # Determine if it's an ADR
        is_adr = False
        if adr_dir and src.is_relative_to(adr_dir):
            is_adr = True
            adr_info = parse_adr(src.name, (root / src).read_text(encoding="utf-8"))
            if not adr_info:
                continue  # skip non‑conforming
            concept = f"adr-{adr_info['number']}"
            title = f"ADR-{adr_info['number']}: {adr_info['title']}"
            tldr = adr_info["tldr"]
        else:
            # Normal doc: concept name from config mapping or filename
            concept = None
            for mconcept, paths in config["concept_mapping"].items():
                if rel in paths or src.name in paths:
                    concept = mconcept
                    break
            if not concept:
                concept = src.stem.lower().replace("_", "-").replace(" ", "-")
            title = concept.replace("-", " ").title()
            tldr = ""  # will be extracted below

        # Read full content
        content = (root / src).read_text(encoding="utf-8")
        sections = extract_sections(content)

        if not is_adr:
            tldr = extract_tldr(sections) or extract_summary(content)
            rules = extract_rules(sections)
            details = sections.get("Details", "") or extract_summary(content)
        else:
            rules = ""
            details = sections.get("Details", "") or content
            # whole body could be long, but keep it compact?
            # We'll take first 500 chars after H1
            # Trim details to a reasonable chunk
            details = extract_summary(
                content
            )  # re-use summary as details for compactness

        # Collect wikilinks from source
        related_links = set()
        for m in re.finditer(r"\[\[([^\]]+)\]\]", content):
            related_links.add(m.group(1))
        related_str = "\n".join(f"- [[{r}]]" for r in sorted(related_links)) or "None"

        source_link = f"../{rel}"  # relative from output dir to source

        page = f"""# {title}

## TL;DR
{tldr}

## Details
{details}

## Rules
{rules if rules else "No specific rules extracted."}

## Related
{related_str}

## Source
- [{src.name}]({source_link})
"""
        out_file = output_dir / f"{concept}.md"
        out_file.write_text(page, encoding="utf-8")
        concept_pages[concept] = (rel, tldr)

    # ------------------------------------------------------------------
    # Generate catalog.md
    # ------------------------------------------------------------------
    catalog_lines = ["# Wiki Catalog", ""]
    for concept, (src, tldr) in sorted(concept_pages.items()):
        one_liner = tldr.split(".")[0].strip()  # first sentence
        if not one_liner:
            one_liner = "No summary"
        catalog_lines.append(
            f"- [[{concept}]] — {one_liner} "
            f"(source: {src}, ingested: {datetime.now(UTC).strftime('%Y-%m-%d')})"
        )
    (output_dir / "catalog.md").write_text("\n".join(catalog_lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # Generate _index.md (category grouping)
    # ------------------------------------------------------------------
    index_lines = ["# Project Knowledge Base", ""]
    categorized = defaultdict(list)  # category -> list of (concept, src)

    if config["diataxis_layout"]:
        # Use Diataxis quadrant names as categories
        for concept, (src, _) in concept_pages.items():
            # Determine quadrant from first path component
            top = src.split("/")[0] if "/" in src else "other"
            if top in DIATAXIS_QUADRANTS:
                cat = config["category_labels"].get(top, top.replace("-", " ").title())
            else:
                cat = "General"
            categorized[cat].append(concept)
        # Append ADRs separately if any
        adr_concepts = [c for c in concept_pages if c.startswith("adr-")]
        if adr_concepts:
            categorized[config["category_labels"]["decisions"]] = sorted(adr_concepts)
        # Order categories as tutorials, how-to, concepts,
        # api-reference, decisions, general
        order = [
            config["category_labels"]["getting-started"],
            config["category_labels"]["how-to"],
            config["category_labels"]["concepts"],
            config["category_labels"]["api-reference"],
            config["category_labels"]["decisions"],
        ]
        for cat in order:
            if cat in categorized:
                del categorized[cat]  # we'll reinsert in order later
        sorted_cats = order + sorted(categorized.keys())
    else:
        # Group by top-level folder
        for concept, (src, _) in concept_pages.items():
            group = src.split("/")[0] if "/" in src else "General"
            categorized[group].append(concept)
        sorted_cats = sorted(categorized.keys())

    for cat in sorted_cats:
        if cat not in categorized:
            continue
        index_lines.append(f"## {cat}")
        for concept in sorted(categorized[cat]):
            index_lines.append(f"- [[{concept}]]")
        index_lines.append("")

    # Add Analysis section if analysis/_index.md exists (it might after synthesis)
    if (output_dir / "analysis" / "_index.md").exists():
        index_lines.append("## Analysis")
        index_lines.append("- [[analysis/_index|Analysis Index]]")

    (output_dir / "_index.md").write_text("\n".join(index_lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # Append to log.md
    # ------------------------------------------------------------------
    log_path = output_dir / "log.md"
    entry = (
        f"## [{datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}] ingest\n"
        f"- Ingested {len(src_files)} source files → {len(concept_pages)} wiki pages.\n"
    )
    with open(log_path, "a") as f:
        f.write(entry)

    print(
        f"Ingested {len(src_files)} source files → "
        "{len(concept_pages)} pages in {output_dir}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Build LLM wiki from Diataxis/ADR docs."
    )
    parser.add_argument(
        "--docs-dir", default=None, help="Overrides source dirs (comma separated)"
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--config", default=None, help="Path to llm-wiki-config.yaml")
    args = parser.parse_args()

    root = Path.cwd()
    config = load_config(root, args.config)

    if args.docs_dir:
        config["source_dirs"] = [d.strip() for d in args.docs_dir.split(",")]

    config["output_dir"] = args.output_dir
    build_wiki(config, root)


if __name__ == "__main__":
    main()
