# Link Type Reference

## Supported Formats & Syntax (Cache)

**1. GitHub Markdown (Default for all new docs)**
* **Standard:** `[display text](path/to/file.md)` or `[text](../relative/path.md)`
* **Absolute:** `[text](/absolute/from/repo/root.md)`
* **External:** `[text](https://external.example.com/page)`
* **Anchors:** `[text](#section-name)` or `[text](other-page.md#section-name)`

**2. Obsidian Wikilinks**
* **Standard:** `[[page-name]]` or `[[folder/page-name]]`
* **With Alias:** `[[page-name|display text]]`
* **Anchors:** `[[page-name#heading]]` or `[[page-name#heading|display text]]`
* **Rules:** No `.md` extension needed. Names are vault-unique (no relative paths). Embeds use `![[page-name]]`. Block references use `[[page-name#^block-id]]`.

**3. Confluence**
* **Space Path:** `[display text](https://wiki.example.com/display/SPACE/Page+Title)`
* **Page ID:** `[display text](https://wiki.example.com/pages/viewpage.action?pageId=123456)`

**4. YouTrack Articles & Issues**
* **Articles:** `[DCE-A-01](https://mgourlis.youtrack.cloud/articles/DCE-A-01)`
* **Issues:** `[DCE-42](https://mgourlis.youtrack.cloud/issue/DCE-42)`

**5. arch42 Cross-References**
* **Syntax:** Wrap in `**` for emphasis inline within prose (not a standard markdown link).
* **Examples:** `**arch42 §N**` or `**arch42 §5.2.3**`

---

## Conversion Algorithms & Rules

### GitHub → Obsidian
* `[text](page.md)` → `[[page|text]]`
* `[text](folder/page.md)` → `[[folder/page|text]]`
* `[text](page.md#section)` → `[[page#section|text]]`
* **Rules:** Strip `.md` extension. If text == page name, omit the alias (e.g., use `[[page]]`, not `[[page|page]]`). External HTTP(S) URLs remain unchanged.

### Obsidian → GitHub
* `[[page]]` → `[page](page.md)`
* `[[page|text]]` → `[text](page.md)`
* `[[folder/page]]` → `[page](../folder/page.md)` *(or relative path)*
* `[[page#section]]` → `[section](page.md#section)`
* **Rules:** Add `.md` extension. Resolve accurate relative paths from the source file's location. If an external URL is embedded (`[[page|https://...]]`), convert to `[page](https://...)`.

### GitHub → Confluence
* **Algorithm:** Maps `.md` paths to Confluence space keys or page IDs. External URLs stay as-is.
* **Requirement:** Needs base Confluence URL and page mapping file/convention as input.

### GitHub → YouTrack
* **Algorithm:** Converts ONLY ADR and KB patterns. All other internal links stay as GitHub Markdown.
* **Mapping:** `adr/ADR-NNN-*.md` → `https://<youtrack-instance>/articles/DCE-A-NN`
* **Requirement:** Needs YouTrack instance URL as input.

### General Limitations
* Bidirectional conversion for GitHub ↔ Obsidian is lossless for standard cases.
* Anchor/fragment references may break if heading text differs between source and target.
* Confluence and YouTrack conversions require external variables to run.
