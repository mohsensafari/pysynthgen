# Contributing & release policy

## Branching model

Trunk-based development around a single long-lived branch, `main`.

- `main` is always releasable and protected — no direct pushes; changes land via PR.
- Do work on short-lived branches named by type: `feat/…`, `fix/…`, `chore/…`,
  `docs/…`, `refactor/…`.
- Open a pull request into `main`. CI (lint, types, tests across supported Python
  versions) must pass before merge.
- **Squash-merge** PRs. The squash commit message is taken from the PR title, so the
  **PR title must follow Conventional Commits** (enforced by a CI check). That title
  is what drives the automated version bump.

## Conventional Commits

Commit messages (and PR titles) follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(optional scope): <description>

[optional body]

[optional footer(s)]
```

Version impact when merged to `main`:

| Commit type | Example | Release |
|-------------|---------|---------|
| `fix:` | `fix: clamp normal ints to min` | patch (`0.1.1` → `0.1.2`) |
| `feat:` | `feat: add jsonl sink` | minor (`0.1.1` → `0.2.0`) |
| `feat!:` / `BREAKING CHANGE:` footer | breaking API change | minor while pre-1.0 (see below), else major |
| `perf:` | `perf: speed up category draw` | patch |
| `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `build:`, `style:` | | no release |

While the project is pre-1.0 (`0.x`), breaking changes bump the **minor** version
(`major_on_zero = false`) rather than jumping to `1.0.0`. Once we ship `1.0.0`,
breaking changes bump the major version.

## Automated release pipeline

Releases are fully automated — you never tag or publish by hand.

On every push to `main` (i.e. every merged PR), [`.github/workflows/release.yml`](.github/workflows/release.yml):

1. Runs the quality gate (ruff, mypy, pytest).
2. Runs [python-semantic-release](https://python-semantic-release.readthedocs.io/):
   analyzes commits since the last tag, and **if** any are release-triggering:
   - computes the next semantic version,
   - updates `[project].version` in `pyproject.toml`,
   - updates `CHANGELOG.md`,
   - commits (`chore(release): X.Y.Z [skip ci]`) and creates the `vX.Y.Z` git tag,
   - creates a GitHub Release.
3. Builds the sdist + wheel and **publishes to PyPI** via Trusted Publishing (OIDC —
   no stored token).

If no commits since the last release are release-triggering, nothing is published.

### One-time setup (maintainers)

These are configured once on the hosting side:

1. **PyPI Trusted Publisher** — on <https://pypi.org>, add a trusted publisher for the
   `pysynthgen` project pointing at:
   - Owner: `mohsensafari`, Repository: `pysynthgen`
   - Workflow: `release.yml`, Environment: `pypi`

   If the project has never been published, add it as a *pending* publisher first.
2. **GitHub `pypi` environment** — exists on the repo; optionally add required
   reviewers to gate publishing.
3. **Pushing the release commit to protected `main`** — the release job pushes the
   version bump back to `main`. Either allow the GitHub Actions bot to bypass branch
   protection, or add a `RELEASE_TOKEN` repository secret (a fine-grained PAT with
   `contents: write`) which the workflow uses in preference to `GITHUB_TOKEN`.
