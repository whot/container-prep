# container-prep

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

Changing the tag will rebuild the container image for that distro/version.

### Example

The recommended setup uses two workflow files: one that defines
container tags and builds images, and one that runs the actual CI.

It's an artifact of GitHub's security boundaries and it will be an effective
requirement for most projects to structure the workflows this way.

#### `.github/workflows/containers.yml`

This file holds all container tags in one place. It triggers on
`push` when the file itself changes (so fork contributors can build
images in their fork's registry) and is callable from the CI workflow
via `workflow_call`.

```yaml
name: Containers

on:
  push:
    branches: ['**']
    paths:
      - '.github/workflows/containers.yml'

  workflow_call:
    outputs:
      fedora-image:
        value: ${{ jobs.fedora.outputs.image }}
      ubuntu-image:
        value: ${{ jobs.ubuntu.outputs.image }}

permissions:
  contents: read
  packages: write

# ── All tags in one place ──
env:
  FEDORA_TAG: '2025-07-27.0'        # bump when deps change
  UBUNTU_TAG: '2025-07-27.0'

jobs:
  fedora:
    runs-on: ubuntu-latest
    outputs:
      image: ${{ steps.prep.outputs.image }}
    steps:
      - uses: actions/checkout@v7
      - id: prep
        uses: whot/container-prep/.github/actions/container-prep@main
        with:
          base-image: 'fedora:44'
          tag: ${{ env.FEDORA_TAG }}
          packages: 'gcc gcc-c++ meson ninja-build'

  ubuntu:
    runs-on: ubuntu-latest
    outputs:
      image: ${{ steps.prep.outputs.image }}
    steps:
      - uses: actions/checkout@v7
      - id: prep
        uses: whot/container-prep/.github/actions/container-prep@main
        with:
          base-image: 'ubuntu:24.04'
          tag: ${{ env.UBUNTU_TAG }}
          packages: 'gcc libc6-dev meson ninja-build pkg-config'
```

#### `.github/workflows/ci.yml`

The main CI workflow. Calls `containers.yml` for images, then runs
the build matrix.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  containers:
    uses: ./.github/workflows/containers.yml
    permissions:
      contents: read
      packages: write

  build:
    needs: containers
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - name: fedora-44
            image: fedora-image
          - name: ubuntu-24.04
            image: ubuntu-image
    container: ${{ needs.containers.outputs[matrix.image] }}
    steps:
      - uses: actions/checkout@v7
      - run: |
          meson setup build
          ninja -C build
          ninja -C build test
```

#### Why two files?

Splitting the container definitions into a separate file enables
fork PR support (see [Fork PRs](#fork-prs) below) and keeps all
tags in one place for easy maintenance.

### How it works

The distro/version/tag triplet is used to construct an image path
in the form `ghcr.io/<project>/<repo>/<distro>/<version>:<tag>`.
The action  uses `skopeo inspect` to check whether that tag already exists,
falling back to `ghcr.io/<user>/<repo>/<distro>/<version>:<tag>` (see
[Fork PRs](#fork-prs) below). If neither image exists, `buildah` creates a
container from the base image, runs the distro-appropriate package manager to
install `packages`, commits the image and pushes it to the registry.

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

Most projects will have a `workflow.yml` that only runs the CI
on pull request and merges into `main`:
```yml
on:
  push:
    branches: [main]
  pull_request:
```
The problem here is that in pull request from a user's forked repository
the `GITHUB_TOKEN` cannot push images to either the upstream or the fork's
registry. This is a GitHub security setting and cannot be circumvented (we
can't pass secrets either so even an access token wouldn't help).

The action handles this with a two-step cache check:

1. **Check the upstream registry** — if the upstream project already has
   an image with the same tag, the fork PR reuses it directly.
2. **Check the fork's registry** — if the upstream image doesn't exist,
   the action checks whether the fork already has a cached image.

Most fork PRs don't change the container tags, so step 1 succeeds
and no build is needed.

When a fork contributor **does** need to bump a tag (e.g. to add a new
build dependency), the split-workflow setup handles it:

1. The contributor edits `containers.yml` in their fork (bumps the tag).
2. They push to their fork.  The `push` trigger fires because the file
   changed, and the fork's `GITHUB_TOKEN` can write to the fork's own
   registry — the image is built and pushed to
   `ghcr.io/<fork-owner>/<fork-repo>/…`.
3. The contributor opens (or updates) the PR against upstream.
4. The upstream PR workflow calls `containers.yml` via `workflow_call`.
   The action finds the image in the fork's registry (step 2 above)
   and uses it.
5. Once the PR is merged into the upstream project, the `GITHUB_TOKEN`
   has write access to the registry and will rebuild the image. Future
   PRs will thus use the newly merged image.

**Note:** If the fork's build is still running when the upstream PR
workflow starts, the image won't be found yet and the job will fail.
Re-run the failed jobs once the fork's build completes.

## License

MIT
