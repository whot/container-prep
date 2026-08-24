# CLAUDE.md

## Project overview

Reusable GitHub Actions for CI container management. The main action is
`container-prep` which builds a container image and pushes it to ghcr.io,
skipping the build when a cached image with the same tag already exists.

Inspired by [freedesktop.org ci-templates](https://gitlab.freedesktop.org/freedesktop/ci-templates)
but for GitHub Actions instead of GitLab CI.

## Repository structure

```
action.yml                    # Composite action definition (inputs/outputs/env)
container-prep.py             # Implementation (python)
.github/
  workflows/
    test-build.yml            # CI: matrix build across distros
    test-features.yml         # CI: feature tests (cache, exec, workdir, fork PRs, etc.)
```

## Key concepts

- The action uses `buildah` and `skopeo` (pre-installed on GitHub runners)
- Image path: `ghcr.io/<owner>/<repo>/<distro>/<version>:<tag>`
- Tag acts as cache key -- same tag = same image, bump tag = rebuild
- Fork PRs auto-detect and check both upstream and fork registries for cached images
- Package manager is auto-detected from the distro name in `base-image`

## Development guidelines

- The script must work without ruff warnings and be formatted with ruff format
- Test new features in `test-features.yml` as separate jobs
- Test distro support in `test-build.yml` via the matrix
- Tests run the action via `uses: ./` or invoke the script directly
  with env vars for scenarios that can't be tested through the action
  (e.g. fork PR simulation)

## Commit style

- Prefix with component: `container-prep:` for action changes
- Body should explain *why*, not just *what*
- Include `Assisted-by: Claude:claude-opus-4-6` in all commits
