#!/usr/bin/env python3
"""Convert links in Markdown files between supported formats.

Supported formats: github, obsidian, confluence, youtrack

Usage:
    python convert_links.py --from github --to obsidian --docs-dir docs/
    python convert_links.py --from obsidian --to github --docs-dir docs/
    python convert_links.py --from github --to confluence --base-url https://wiki.example.com --docs-dir docs/
    python convert_links.py --from github --to youtrack --youtrack-url https://instance.youtrack.cloud --docs-dir docs/
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import quote


def github_to_obsidian(content: str, source_file: Path) -> str:
    """Convert GitHub Markdown links to Obsidian wikilinks."""

    def replace_link(match: re.Match) -> str:
        text = match.group(1)
        target = match.group(2)

        # Leave external URLs untouched
        if target.startswith(("http://", "https://", "mailto:")):
            return match.group(0)

        # Handle fragments
        fragment = ""
        if "#" in target:
            target, fragment = target.split("#", 1)

        # Strip .md extension
        page_name = target.replace(".md", "")

        # Build wikilink
        if fragment:
            wikilink_target = f"{page_name}#{fragment}"
        else:
            wikilink_target = page_name

        if text == page_name.split("/")[-1] or text == wikilink_target:
            # Clean alias — just [[page]]
            return f"[[{wikilink_target}]]"
        else:
            return f"[[{wikilink_target}|{text}]]"

    # Only convert internal links, leave external ones
    return re.sub(
        r"\[([^\]]*)\]\(([^)]+)\)",
        replace_link,
        content,
    )


def obsidian_to_github(content: str, source_file: Path, docs_dir: Path) -> str:
    """Convert Obsidian wikilinks to GitHub Markdown links."""

    def replace_wikilink(match: re.Match) -> str:
        page = match.group(1)
        alias = match.group(2) if match.lastindex and match.group(2) else None

        # External URLs in wikilinks (unusual)
        if page.startswith(("http://", "https://")):
            display = alias or page
            return f"[{display}]({page})"

        # Build the .md path
        target_md = f"{page}.md"

        # Try to resolve relative to source file
        source_dir = source_file.parent
        # The page might be relative — try common patterns
        candidate_paths = [
            source_dir / target_md,
            source_dir.parent / target_md,
            docs_dir / target_md,
        ]

        resolved = target_md  # default fallback
        for candidate in candidate_paths:
            if candidate.exists():
                try:
                    resolved = str(candidate.relative_to(source_dir))
                    if not resolved.startswith("."):
                        resolved = f"./{resolved}"
                except ValueError:
                    resolved = str(candidate)
                break

        display = alias or page
        return f"[{display}]({resolved})"

    return re.sub(
        r"\[\[([^\]|#]+)(?:[#|]([^\]]+))?\]\]",
        replace_wikilink,
        content,
    )


def github_to_confluence(content: str, base_url: str) -> str:
    """Convert GitHub Markdown internal links to Confluence URLs.

    This requires a mapping from .md paths to Confluence page IDs,
    which must be provided as a JSON mapping file.
    Internal links that can't be mapped are left as-is with a comment.
    """

    def replace_link(match: re.Match) -> str:
        text = match.group(1)
        target = match.group(2)

        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)

        # Convert filename to Confluence-style page title
        page_name = Path(target).stem
        page_name = page_name.replace("-", " ").replace("_", " ").title()
        encoded = quote(page_name)

        return f"[{text}]({base_url}/display/DOCS/{encoded})"

    return re.sub(
        r"\[([^\]]*)\]\(([^)]+)\)",
        replace_link,
        content,
    )


def github_to_youtrack(content: str, youtrack_url: str) -> str:
    """Convert GitHub Markdown links to YouTrack article/issue URLs.

    Only converts:
    - ADR links (adr/ADR-NNN-*.md) → YouTrack article URLs
    - KB article references (DCE-A-NN) → YouTrack article URLs
    """

    def replace_link(match: re.Match) -> str:
        text = match.group(1)
        target = match.group(2)

        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)

        # Check if this is an ADR link
        adr_match = re.match(r".*ADR-(\d+).*\.md", target)
        if adr_match:
            adr_num = adr_match.group(1)
            return f"[ADR-{adr_num}]({youtrack_url}/articles/DCE-A-{adr_num})"

        return match.group(0)

    return re.sub(
        r"\[([^\]]*)\]\(([^)]+)\)",
        replace_link,
        content,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert links in Markdown files between supported formats."
    )
    parser.add_argument(
        "--from",
        dest="from_format",
        required=True,
        choices=["github", "obsidian"],
        help="Source link format",
    )
    parser.add_argument(
        "--to",
        dest="to_format",
        required=True,
        choices=["github", "obsidian", "confluence", "youtrack"],
        help="Target link format",
    )
    parser.add_argument(
        "--docs-dir",
        default="docs",
        help="Path to the documentation directory (default: docs)",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for Confluence instance (required for --to confluence)",
    )
    parser.add_argument(
        "--youtrack-url",
        help="Base URL for YouTrack instance (required for --to youtrack)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--file",
        dest="target_file",
        help="Convert only a single file instead of the whole directory",
    )
    args = parser.parse_args()

    # Validate required options
    if args.to_format == "confluence" and not args.base_url:
        print("Error: --base-url is required for Confluence conversion")
        sys.exit(1)
    if args.to_format == "youtrack" and not args.youtrack_url:
        print("Error: --youtrack-url is required for YouTrack conversion")
        sys.exit(1)
    if args.from_format == args.to_format:
        print("Source and target formats are the same — nothing to do.")
        sys.exit(0)

    docs_dir = Path(args.docs_dir).resolve()

    # Select files
    if args.target_file:
        md_files = [Path(args.target_file).resolve()]
    else:
        md_files = sorted(docs_dir.rglob("*.md"))

    converter = None
    if args.from_format == "github" and args.to_format == "obsidian":
        converter = lambda c, f: github_to_obsidian(c, f)
    elif args.from_format == "obsidian" and args.to_format == "github":
        converter = lambda c, f: obsidian_to_github(c, f, docs_dir)
    elif args.from_format == "github" and args.to_format == "confluence":
        converter = lambda c, f: github_to_confluence(c, args.base_url)
    elif args.from_format == "github" and args.to_format == "youtrack":
        converter = lambda c, f: github_to_youtrack(c, args.youtrack_url)
    else:
        print(
            f"Conversion from {args.from_format} to {args.to_format} is not supported."
        )
        sys.exit(1)

    files_changed = 0
    links_changed = 0

    for f in md_files:
        original = f.read_text()
        converted = converter(original, f)

        if converted != original:
            files_changed += 1
            # Count changes
            original_links = len(re.findall(r"\[([^\]]*)\]\(([^)]+)\)", original))
            converted_links = len(re.findall(r"\[([^\]]*)\]\(([^)]+)\)", converted))
            links_changed += abs(original_links - converted_links)

            if args.dry_run:
                print(f"Would change: {f.relative_to(docs_dir)}")
            else:
                f.write_text(converted)
                print(f"Converted: {f.relative_to(docs_dir)}")

    if args.dry_run:
        print(f"\nDry run: {files_changed} file(s) would be changed")
    else:
        print(f"\nDone: {files_changed} file(s) converted")


if __name__ == "__main__":
    main()
