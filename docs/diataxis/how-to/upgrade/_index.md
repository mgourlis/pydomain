# Upgrade How-to Guides

> **Adoption Level:** All levels

What to change in your code and tests when you move to a new pydomain release. The release notes on GitHub
list commit subjects only; these pages explain each behaviour change and what to do about it.

## Releases

| Guide | Release | Status |
|-------|---------|--------|
| [Upgrade to the next release](upgrade-to-next-release.md) | next after 0.3.1 | In progress |
| [Upgrade to 0.3.1](upgrade-to-0.3.1.md) | 0.3.1 | Released |

## Writing an entry

Every pull request that changes documented behaviour adds one entry to the page of the next release, in
the same PR as the change. Use this shape:

```markdown
### <One line: what behaves differently> (DCE-NN)

**What changed:** the old behaviour, the new behaviour, and the contract (ADR, docstring or page) it now
follows.

**Who is affected:** the code or tests that notice, e.g. "tests that reuse one `FakeUnitOfWork` across
commands", "adapters that implement `OutboxStore`".

**What to do:** the concrete change, with a short before/after example when code must change. Write
"nothing" if the change only removes wrong behaviour that no correct code relied on.
```

Group entries under **Breaking**, **Behaviour changes** and **Deprecations**. Breaking entries ship only in a
`release/v*` release. When a release is tagged, rename its page to `upgrade-to-<version>.md`, mark it
**Released** here, and start a new `upgrade-to-next-release.md`.

## See Also

- [ADR-066](../../../adr/ADR-066-test-first-fixes-with-documentation.md): every behaviour change ships with its upgrade-notes entry
- [Versioning and compatibility](../../../arch42/07-deployment-view.md#75-versioning-and-compatibility): what counts as a patch, minor or major change
