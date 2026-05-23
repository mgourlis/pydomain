# Commit & Release Guide

## Table of Contents

1. [Branch Structure](#1-branch-structure)
2. [Merge Strategy Guide](#2-merge-strategy-guide)
3. [Commit Message Format](#3-commit-message-format)
4. [Fixing Bad Commits](#4-fixing-bad-commits)
5. [Breaking Changes](#5-breaking-changes)
6. [Automated Workflows](#6-automated-workflows)
7. [Release Process](#7-release-process)
8. [Emergency Hotfixes](#8-emergency-hotfixes)
9. [Major Releases (Breaking Changes)](#9-major-releases-breaking-changes)
10. [Environment & Protection Setup](#10-environment--protection-setup)
11. [FAQ & Troubleshooting](#11-faq--troubleshooting)

---

## 1. Branch Structure

We use three long‑living branches:

| Branch | Purpose |
| --- | --- |
| **`dev`** | Daily integration. All feature/fix branches are merged here via pull requests. |
| **`staging`** | Pre‑release testing. Synced from `dev` via a pull request. |
| **`main`** | Production code. Synced from `staging` via a pull request. **Tags are created on `main` to trigger releases.** |

> **Important:** Merge commits from sync PRs (`dev → staging` and `staging → main`) are **automatically excluded** from the release changelog – only your feature/fix commits will appear.

---

## 2. Merge Strategy Guide

To keep our automated changelogs accurate and our Git history clean, you **must** use the correct GitHub merge button depending on the context.

### Quick Reference

| Scenario | Source Branch | Target Branch | GitHub Merge Button to Use |
| --- | --- | --- | --- |
| **Normal Development** | `feature/...` | `dev` | **Squash and merge** |
| **Pre-Release QA** | `dev` | `staging` | **Create a merge commit** |
| **Production Release** | `staging` | `main` | **Create a merge commit** |
| **Emergency Hotfix** | `hotfix/...` | `main` | **Squash and merge** |
| **Sync-Back (Automated)** | `sync/main-to-...` | `dev` / `staging` | **Create a merge commit** |
| **Major Release (V2)** | `release/v*` | `main` | **Create a merge commit** |

- **Squash and Merge:** Use for bringing new code into `dev` or `main`. It condenses messy commit history into a single, clean Conventional Commit.
- **Create a Merge Commit:** Use for syncing long‑living branches. It preserves the exact commit SHAs. If you squash during a sync, Git rewrites the SHAs, which breaks the automated changelog generator. *(Note: Syncing with a merge commit will result in the target branch being "1 commit ahead" due to the empty merge commit. This is expected and healthy.)*

---

## 3. Commit Message Format

We follow a **simplified Conventional Commits** pattern with an **optional YouTrack issue prefix** and **optional scope**.

### Format

```
<optional-issue-prefix>: <type>(<optional-scope>): <description>
```

- **`<optional-issue-prefix>`** – e.g., `PRJ-123:` (include the colon and space). Omit for trivial changes.
- **`<type>`** – one of the types listed below (lowercase).
- **`(<optional-scope>)`** – the part of the codebase affected, e.g., `(api)`, `(README)`.
- **`<description>`** – concise, imperative mood, no trailing period. *(A trailing period will not break the build, but omitting it is our standard style).*

### Commit Types & Changelog Inclusion

| Type | When to use | Appears in changelog? |
| --- | --- | --- |
| `feat` | New feature for users | ✅ Yes |
| `fix` | Bug fix for users | ✅ Yes |
| `perf` | Performance improvement | ✅ Yes |
| `docs` | Documentation only | ❌ No (filtered) |
| `style` | Code style/formatting (no production change) | ❌ No |
| `refactor` | Code change that neither fixes a bug nor adds a feature | ❌ No |
| `test` | Adding or correcting tests | ❌ No |
| `chore` | Build process, tooling, dependencies | ❌ No |
| `ci` | CI/CD configuration | ❌ No |
| `build` | Build system or external dependencies | ❌ No |
| `revert` | Reverts a previous commit | ❌ No |

### Enforcing the Format Locally

To catch bad commit messages before pushing, install our `pre-commit` hook. Add this to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: commit-message-check
        name: Check commit message formatting
        entry: bash -c 'grep -qE "^([A-Z]+-[0-9]+:[[:space:]]*)?(feat|fix|perf|docs|style|refactor|test|chore|ci|build|revert)(\([a-zA-Z0-9_-]+\))?[!:]?[[:space:]]+.+" "$1" || { echo "❌ Invalid commit message format. Expected: <PRJ-123:> <type>(<scope>): <desc>"; exit 1; }' --
        language: system
        stages: [commit-msg]
```

Run `pre-commit install --hook-type commit-msg` once to enable it. *(A GitHub Action also runs on all PRs to act as a final gatekeeper, but the regex allows for minor whitespace forgiveness after the YouTrack prefix).*

---

## 4. Fixing Bad Commits

If you pushed a commit with a typo in the message and the CI pipeline blocked your PR, here is how to fix it locally and update your PR.

### Scenario A: Fixing the *very last* commit

1. Run `git commit --amend`
2. Your text editor will open. Fix the message, save, and exit.
3. Force push: `git push --force-with-lease origin your-branch-name`

### Scenario B: Fixing older commits (or multiple commits)

1. Run an interactive rebase for the last `N` commits (e.g., 3 commits back): `git rebase -i HEAD~3`
2. Change the word `pick` to `reword` (or `r`) next to the commits you want to fix. Save and close.
3. Git will pause at each marked commit. Fix the message, save, and close.
4. Force push: `git push --force-with-lease origin your-branch-name`

---

## 5. Breaking Changes

A **breaking change** makes your library backward incompatible (e.g., removing a function, changing a public API).

### How to Mark a Breaking Change

1. **Add `!` after the type (and optional scope)** – recommended
   Example: `PRJ-123: feat(api)!: change login endpoint signature`
2. **Add a `BREAKING CHANGE:` footer** after a blank line
   Example:
   ```
   PRJ-456: feat: redesign configuration

   BREAKING CHANGE: The old config format is no longer supported.
   ```

> **Note:** Breaking changes will be **automatically blocked** from being merged into `dev`, `staging`, or `main` during normal releases. See [Major Releases](#9-major-releases-breaking-changes) for the correct workflow.

---

## 6. Automated Workflows

We have several key workflows in `.github/workflows/`:

- **`publish.yml`** : Triggered by `v*` tags. Builds and publishes to PyPI, generates the changelog (ignoring `chore`, `docs`, etc.), creates a GitHub Release, and **automatically opens sync-back PRs** to `dev` and `staging`.
- **`block-breaking.yml`** : Scans all PR commits for `!:` or `BREAKING CHANGE:` markers. It fails the PR unless the source branch is named `release/v*`.
- **`lint-commits.yml`** : Scans PRs to ensure all commit messages match our Conventional Commits regex.
- **`tests-and-lint.yml`** : Runs on all pushes and PRs to `dev`, `staging`, and `main`. It uses `uv` to manage dependencies, lints and formats the code with `ruff`, performs type checking with `mypy`, runs tests via `pytest`, and uploads coverage reports to Codecov.

---

## 7. Release Process

### Step‑by‑Step for a Normal (Minor/Patch) Release

1. Ensure all desired features/bugfixes are merged into `dev`, synced to `staging` (via Merge Commit), and finally synced to `main` (via Merge Commit).
2. **Checkout `main` locally**:
   ```bash
   git checkout main
   git pull origin main
   ```
3. **Choose the next version number** according to [Semantic Versioning](https://semver.org/) (x.y.Z for patches, x.Y.0 for minor features).
4. **Create and push the tag**:
   ```bash
   git tag v1.2.0
   git push origin v1.2.0
   ```
5. The automation (`publish.yml`) will take over to publish to PyPI and generate release notes.

> **Do not create the release manually in the GitHub UI**. Always push the tag and let automation do the work.

---

## 8. Emergency Hotfixes

When a critical bug is found in production (`main`), it must be fixed immediately without waiting for `dev` or `staging` to clear.

1. **Branch directly from `main`**:
   ```bash
   git checkout -b hotfix/fix-login-crash main
   ```
2. **Commit your fix**:
   ```bash
   git commit -m "PRJ-999: fix(auth): resolve login crash"
   ```
3. **Open a PR directly to `main`** and **Squash and Merge**.
4. **Create a new Patch tag on `main`**:
   ```bash
   git tag v1.2.1 && git push origin v1.2.1
   ```
5. The automation will release the hotfix to PyPI.
6. **Important:** The workflow will automatically create PRs to sync `main` back down to `dev` and `staging`. **Do not ignore these PRs.** Merge them via **Create a merge commit** immediately so the hotfix isn't overwritten.

> **Conflict handling:** If a sync‑back PR shows conflicts (e.g., because `dev` has moved forward), resolve them manually using `git merge` locally or the GitHub conflict editor. The goal is to bring the hotfix into the lower branches so future releases include it. This is normal in GitFlow and must be handled carefully.

---

## 9. Major Releases (Breaking Changes)

Because our automation blocks breaking changes in normal PRs, major version bumps use a dedicated branch structure.

1. **Create a dedicated branch** starting from `main`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b release/v2
   ```
2. **Merge your breaking change branches** into `release/v2`.
3. **Open a PR from `release/v2` to `main`**. The `block-breaking.yml` workflow will detect the `release/v*` branch name and **automatically allow** the breaking changes through.
4. Merge into `main` using **Create a merge commit**.
5. Tag `main` with the new major version:
   ```bash
   git tag v2.0.0
   git push origin v2.0.0
   ```
6. **After the tag is pushed**, the `sync-back` job will open PRs from `main` to `dev` and `staging`. These PRs may have **significant conflicts** because `dev` and `staging` still contain the old `v1.x` code. Resolve the conflicts carefully (e.g., by manually merging `main` into `dev` locally), then merge the PRs using **Create a merge commit**. This brings the major version into the development branches.

> **Why does GitFlow handle this naturally?** In classic GitFlow, after a major release, you would merge the release branch back into `develop`. Our automated sync‑back does exactly that. Conflicts are inevitable when the lower branches diverge – resolving them is a one‑time manual step per major release.

---

## 10. Environment & Protection Setup

### PyPI Environment (`pypi-publish`)

1. Go to repository **Settings** → **Environments** → **New environment**.
2. Name: `pypi-publish`.
3. Under **Deployment branches and tags**:
   - Add branch rule: `main`
   - Add tag rule: `v*`
4. Under **Deployment protection rules**:
   - **Required reviewers**: add at least one maintainer.
   - **Allow administrators to bypass** – check this if you are the sole reviewer (our ruleset already gives you bypass).

### Branch Protection Rules

For each branch (`dev`, `staging`, `main`):

1. **Settings** → **Branches** → **Add branch protection rule**.
2. Branch name pattern: `dev` (repeat for `staging`, `main`).
3. Enable:
   - **Require status checks to pass before merging**
   - Search and select `Block Breaking Changes`, `Lint Commit Messages`, and your CI tests.
4. *(Optional)* **Require pull request reviews** – if enabled, ensure your own user is in the bypass list (our ruleset already handles this).

> For a solo maintainer, requiring reviews is optional. Our ruleset already grants you bypass permissions, so you can approve your own PRs if needed.

---

## 11. FAQ & Troubleshooting

### Q: My commit with `feat:` didn’t appear in the changelog. Why?

- Ensure the commit is on `main` after the sync PRs.
- Confirm you didn’t use a filtered type (`docs`, `chore`, etc.).
- Ensure your sync PRs used "Create a Merge Commit" and not "Squash".

### Q: I pushed a tag but the workflow didn’t run.

- Tag must start with `v` (e.g., `v1.2.0`).
- The tag must exist on `main`. Check the **Actions** tab for permission errors.

### Q: Can I manually edit the release notes after creation?

Yes. The workflow creates the release only once. You can edit the GitHub Release body anytime – it will not be overwritten.

### Q: A sync‑back PR has conflicts. What do I do?

- Conflicts happen when `dev` or `staging` have diverged from `main` (e.g., after a major release or heavy refactoring).
- Resolve them locally: check out the target branch, merge `main` into it, fix conflicts, push. Then the PR will update automatically.
- Alternatively, use GitHub’s web editor to resolve conflicts, then merge the PR using **Create a merge commit**.

### Q: Can I merge a sync‑back PR with squash?

**No.** Always use **Create a merge commit** for sync‑back PRs. Squashing would rewrite history and break future changelog generation.

---

## Summary for Daily Work

| I want to… | Do this |
| --- | --- |
| Add a new feature | `git commit -m "PRJ-123: feat(area): add awesome thing"` |
| Fix a bug | `git commit -m "PRJ-456: fix: correct weird behavior"` |
| Improve performance | `git commit -m "perf(cache): reduce memory usage"` |
| Update documentation | `git commit -m "docs: explain installation"` (excluded from changelog) |
| Make a breaking change | `git commit -m "PRJ-789: feat(api)!: new signature"` – merge into `release/v*` |
| Make a normal release | `git tag v1.2.0 && git push origin v1.2.0` |
| Emergency Hotfix | Branch off `main`, merge to `main`, tag `v1.2.1` |

---

**Questions?** Ask in the team channel. The automation is robust – even if a commit format is slightly off, it won’t break the build; it only affects the changelog.
