# gh-ci-templates

Reusable GitHub Actions for CI container management — inspired by
[freedesktop.org ci-templates](https://gitlab.freedesktop.org/freedesktop/ci-templates).
The ci-templates have one killer feature: as part of the CI run one builds a
(new) container that is then pushed to the project's container registry. As long
as the distro/version/tag triplet remains the same, future CI runs will use
exactly the same image. The CI is thus not exposed to downtimes or rate limits
of remote registries, unexpected package changes in the distribution used, etc.

The run is fully reproducible - pull the same image and you have the same baseline.
See the [ci-templates documentation](https://freedesktop.pages.freedesktop.org/ci-templates/) for
details.

However, the ci-templates are designed for GitLab's CI. This repository is a
port (done by an LLM, at least initially) of that functionality into an
easy-to-use GitHub workflow.

## container-prep

A composite action that builds a container image **only when a tagged
version does not already exist** in the registry. On subsequent runs with
the same tag the build is skipped entirely.

This not only saves time but also ensures that future CI jobs run with exactly
the same package set.

### Example

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

# The action pushes to ghcr.io, which requires write access to packages.
permissions:
  contents: read
  packages: write

jobs:
  container-prep:
    runs-on: ubuntu-latest
    outputs:
      image: ${{ steps.container.outputs.image }}
    steps:
      - uses: whot/gh-ci-templates/.github/actions/container-prep@main
        id: container
        with:
          base-image: 'fedora:44'
          tag: '2025-07-27.0'          # bump this when deps change
          packages: 'gcc gcc-c++ meson ninja-build git python3-pip'
          exec: |
            pip install gcovr
            useradd -m builder

  build:
    needs: container-prep
    runs-on: ubuntu-latest
    container: ${{ needs.container-prep.outputs.image }}
    steps:
      - uses: actions/checkout@v4
      - run: |
          meson setup build
          ninja -C build
          ninja -C build test
```

### How it works

The distro/version/tag triplet is used to construct an image path
in the form `ghcr.io/<owner>/<repo>/<distro>/<version>:<tag>`. 
The action  uses `skopeo inspect` to check whether that tag already exists, if
not it uses `buildah` to create a container from the base image,
runs the distro-appropriate package manager to install `packages`,
commits the image and pushes it to the registry.

### Inputs

| Input           | Required | Default               | Description                                                          |
|-----------------|----------|-----------------------|----------------------------------------------------------------------|
| `base-image`    | yes      | —                     | Base OCI image (e.g. `fedora:44`, `ubuntu:24.04`, `alpine:3.20`)     |
| `tag`           | yes      | —                     | Image tag — bump when content should change                          |
| `packages`      | no       | `''`                  | Space-separated packages to install                                  |
| `suffix`        | no       | `<distro>/<version>`  | Override the image path suffix                                       |
| `registry`      | no       | `ghcr.io`             | Container registry                                                   |
| `token`         | no       | `${{ github.token }}` | Registry auth token                                                  |
| `exec`          | no       | `''`                  | Shell commands to run inside the container after package installation|
| `workdir`       | no       | `/github/workspace`   | Working directory inside the container                               |
| `force-rebuild` | no       | `false`               | Set to `true` to always rebuild                                      |

### Outputs

| Output          | Description |
|-----------------|-------------|
| `image`         | Full image reference for use in `container:` |
| `build-skipped` | `true` if the image already existed |

### Supported distros

Package manager detection is automatic based on the `base-image` name:

| Distro pattern | Package manager |
|----------------|-----------------|
| `alpine`       | `apk`           |
| `arch*`        | `pacman`        |
| `centos*`      | `dnf`           |
| `debian`       | `apt-get`       |
| `fedora`       | `dnf`           |
| `opensuse*`    | `zypper`        |
| `rocky`        | `dnf` (with CRB/EPEL enabled) |
| `ubuntu`       | `apt-get`       |

Unknown distros trigger a warning but do not fail — useful when
`base-image` already contains everything you need.

### Fork PRs

When a pull request comes from a **fork** (i.e. the head repo differs from the
base repo), the action automatically adjusts its behavior:

1. **Check the upstream registry first** — if the upstream project already has an
   image with the same tag, the fork PR reuses it directly.  No build needed.
2. **Check the fork's registry** — if the upstream image doesn't exist, the action
   checks whether the fork already has a cached image.
3. **Build and push to upstream** — if neither registry has the image, the action
   builds it and pushes to the upstream project's registry.

No special tokens or configuration are needed for fork PRs — the default
`GITHUB_TOKEN` has write access to the upstream project's packages.

## License

MIT
