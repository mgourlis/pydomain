# Link Type Reference

## Supported Formats

### GitHub Markdown (default)

Standard relative or absolute Markdown links. This is the default format for all new documents.

**Syntax:**
```markdown
[display text](path/to/file.md)
[display text](../relative/path.md)
[display text](/absolute/from/repo/root.md)
[display text](https://external.example.com/page)
```

**Examples:**
```markdown
[Entities](../concepts/ddd/entities.md)
[How to define an aggregate](../how-to/ddd/define-aggregate.md)
[Pydantic docs](https://docs.pydantic.dev/latest/)
```

**Fragment/anchor links:**
```markdown
[see section below](#section-name)
[other page section](other-page.md#section-name)
```

### Obsidian Wikilinks

Used in Obsidian vaults. Supports aliases with the pipe syntax.

**Syntax:**
```markdown
[[page-name]]
[[page-name|display text]]
[[folder/page-name]]
[[page-name#heading]]
[[page-name#heading|display text]]
```

**Examples:**
```markdown
[[entities]]
[[entities|Entity concepts]]
[[ddd/entities]]
[[entities#identity-equality]]
[[cqrs/commands#command-id|Command identity]]
```

**Key differences from GitHub Markdown:**
- No `.md` extension needed (Obsidian resolves by page name)
- No relative path resolution -- page names are unique within the vault
- Embed syntax: `![[page-name]]` (embeds content, not a link)
- Block references: `[[page-name#^block-id]]`

### Confluence

Absolute URLs to a Confluence wiki instance.

**Syntax:**
```markdown
[display text](https://wiki.example.com/display/SPACE/Page+Title)
[display text](https://wiki.example.com/pages/viewpage.action?pageId=123456)
```

**Examples:**
```markdown
[Architecture Overview](https://wiki.acme.com/display/ENG/Architecture+Overview)
[ADR-001](https://wiki.acme.com/pages/viewpage.action?pageId=987654)
```

### YouTrack Articles / Issues

Absolute URLs to YouTrack articles or issues.

**Article syntax:**
```markdown
[DCE-A-01](https://mgourlis.youtrack.cloud/articles/DCE-A-01)
[Project Overview](https://mgourlis.youtrack.cloud/articles/DCE-A-00)
```

**Issue syntax:**
```markdown
[DCE-42](https://mgourlis.youtrack.cloud/issue/DCE-42)
[Related bug](https://mgourlis.youtrack.cloud/issue/DCE-99)
```

### arch42 Cross-References

Lightweight section references for architecture documentation.

**Syntax:**
```markdown
arch42 §N
arch42 §N.M
```

**Examples:**
```markdown
arch42 §5.2.3 -- AggregateRoot building block
arch42 §8.3 -- Event collection and publish-after-commit
```

These are typically used inline within prose, not as formal Markdown links. Wrap in `**` for emphasis: `**arch42 §5.2**`.

---

## Conversion Rules

### GitHub → Obsidian
| GitHub | Obsidian |
|--------|----------|
| `[text](page.md)` | `[[page\|text]]` |
| `[text](folder/page.md)` | `[[folder/page\|text]]` |
| `[text](page.md#section)` | `[[page#section\|text]]` |
| `[text](https://external.com)` | (unchanged -- external URLs stay as Markdown) |

**Algorithm:**
1. If the URL is an absolute HTTP(S) URL, leave it as-is
2. If the URL is a relative `.md` file, strip the `.md` extension and convert to `[[path|text]]`
3. If text == page name, omit the alias: `[[page]]` not `[[page|page]]`
4. Fragment/anchor references: `#section` stays as `#section` inside the wikilink

### Obsidian → GitHub
| Obsidian | GitHub |
|----------|--------|
| `[[page]]` | `[page](page.md)` |
| `[[page\|text]]` | `[text](page.md)` |
| `[[folder/page]]` | `[page](../folder/page.md)` or `[page](folder/page.md)` |
| `[[page#section]]` | `[section](page.md#section)` |

**Algorithm:**
1. Strip alias if identical to page name
2. Add `.md` extension
3. Determine relative path from the source file's location
4. External URLs embedded in wikilinks: `[[page|https://...]]` → `[page](https://...)` (unusual but handle it)

### GitHub → Confluence
**Algorithm:**
1. Internal `.md` links need a base Confluence URL as input
2. Map `.md` paths to Confluence page IDs or space keys (requires a mapping file or convention)
3. External URLs stay as-is
4. This conversion requires additional input (base URL + page mapping) -- the script prompts for these

### GitHub → YouTrack
**Algorithm:**
1. Only links matching ADR patterns or KB article patterns are converted
2. `adr/ADR-NNN-*.md` → `https://<youtrack-instance>/articles/DCE-A-NN`
3. All other internal links stay as GitHub Markdown
4. Requires a YouTrack instance URL as input

### Conversion Limitations
- Confluence and YouTrack conversions require external instance URLs and/or page ID mappings
- Bidirectional conversion between Obsidian and GitHub is lossless for standard cases
- Anchor/fragment references may break if heading text differs between source and target
- The `convert_links.py` script handles the mechanical transformation; complex mappings may need manual review
