#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# container-prep.sh — check whether a tagged container image exists in the
# registry; if not, build it from a base image and install packages.
#
# Expected environment variables (set by action.yml):
#   INPUT_BASE_IMAGE     — e.g. "fedora:44", "registry.fedoraproject.org/fedora:44"
#   INPUT_TAG            — image tag, e.g. "2025-07-27.0"
#   INPUT_PACKAGES       — space-separated package list (may be empty)
#   INPUT_SUFFIX         — optional image path suffix (default: distro/version)
#   INPUT_REGISTRY       — container registry (default: ghcr.io)
#   INPUT_REGISTRY_USER  — registry login username
#   INPUT_TOKEN          — registry auth token
#   INPUT_EXEC           — shell commands to run after package installation
#   INPUT_WORKDIR        — working directory inside the container
#   INPUT_FORCE_REBUILD  — "true" to skip the cache check
#   INPUT_USER_REPO      — for fork PRs: head repo full_name (e.g. "user/foo")
#   GITHUB_REPOSITORY    — owner/repo (the PR target / upstream repo)
#   GITHUB_OUTPUT        — file path for action outputs

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────
group()     { echo "::group::$1"; }
endgroup()  { echo "::endgroup::"; }
die()       { echo "::error::$1"; exit 1; }

# ── prerequisite check ────────────────────────────────────────────────────
for tool in buildah skopeo; do
    command -v "$tool" >/dev/null || die "$tool is required but not found on this runner"
done

# Split the space-separated package list into an array once so we can
# quote it properly everywhere (avoids SC2086).
read -ra packages <<< "${INPUT_PACKAGES:-}"

# ── parse distro / version from base-image ───────────────────────────────
# Strip any registry prefix: "registry.fedoraproject.org/fedora:44" → "fedora:44"
base_short="${INPUT_BASE_IMAGE##*/}"

if [[ "$base_short" != *:* ]]; then
    die "base-image '${INPUT_BASE_IMAGE}' must include a version tag (e.g. 'fedora:44')"
fi

distro="${base_short%%:*}"
version="${base_short##*:}"

if [[ -z "$distro" || -z "$version" ]]; then
    die "Cannot parse distro/version from base-image '${INPUT_BASE_IMAGE}'"
fi

echo "Distro: $distro  Version: $version"

# ── validate tag ─────────────────────────────────────────────────────────
if [[ ! "${INPUT_TAG}" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.\-]{0,127}$ ]]; then
    die "tag '${INPUT_TAG}' is not a valid OCI tag (must match [a-zA-Z0-9_.-]{1,128}, cannot start with '.' or '-')"
fi

# ── fork PR detection ────────────────────────────────────────────────────
is_fork_pr="false"
if [[ -n "${INPUT_USER_REPO:-}" && "${INPUT_USER_REPO}" != "${GITHUB_REPOSITORY}" ]]; then
    is_fork_pr="true"
    echo "Fork PR detected: ${INPUT_USER_REPO} -> ${GITHUB_REPOSITORY}"
fi

# ── image path ───────────────────────────────────────────────────────────
suffix="${INPUT_SUFFIX:-${distro}/${version}}"
upstream_image="${INPUT_REGISTRY}/${GITHUB_REPOSITORY}/${suffix}:${INPUT_TAG}"

if [[ "$is_fork_pr" == "true" ]]; then
    user_image="${INPUT_REGISTRY}/${INPUT_USER_REPO}/${suffix}:${INPUT_TAG}"
    echo "Upstream image: $upstream_image"
    echo "User image:     $user_image"
else
    echo "Target image: $upstream_image"
fi

# ── registry login ───────────────────────────────────────────────────────
group "Registry login"
echo "${INPUT_TOKEN}" | buildah login \
    --username "${INPUT_REGISTRY_USER}" \
    --password-stdin "${INPUT_REGISTRY}"
endgroup

# ── check whether image already exists ───────────────────────────────────
build_needed="true"
image=""       # will be set to the image reference we end up using

if [[ "${INPUT_FORCE_REBUILD}" != "true" ]]; then
    group "Checking for existing image"

    # Step 1: check the upstream registry (always)
    echo "Checking upstream: $upstream_image"
    if [[ "$is_fork_pr" == "true" ]]; then
        # For fork PRs the token is scoped to the fork, not upstream.
        # Upstream is public so we can inspect without credentials.
        inspect_args=(--no-tags --retry-times 3)
    else
        inspect_args=(--no-tags --retry-times 3
                      --creds "${INPUT_REGISTRY_USER}:${INPUT_TOKEN}")
    fi

    if skopeo inspect "${inspect_args[@]}" \
            "docker://${upstream_image}" >/dev/null 2>&1; then
        echo "Image ${upstream_image} already exists -- skipping build"
        image="$upstream_image"
        build_needed="false"
    elif [[ "$is_fork_pr" == "true" ]]; then
        # Step 2 (fork PRs only): check the user's registry
        echo "Not found upstream -- checking user registry: $user_image"
        if skopeo inspect --no-tags --retry-times 3 \
                --creds "${INPUT_REGISTRY_USER}:${INPUT_TOKEN}" \
                "docker://${user_image}" >/dev/null 2>&1; then
            echo "Image ${user_image} already exists -- skipping build"
            image="$user_image"
            build_needed="false"
        else
            echo "Image not found in either registry -- will build"
        fi
    else
        echo "Image not found -- will build"
    fi

    endgroup
else
    echo "Force-rebuild requested -- skipping cache check"
fi

# Set the target image for building / output
if [[ -z "$image" ]]; then
    if [[ "$is_fork_pr" == "true" ]]; then
        image="$user_image"
    else
        image="$upstream_image"
    fi
fi

# ── build if needed ──────────────────────────────────────────────────────
if [[ "$build_needed" == "true" ]]; then
    group "Building container"

    ctr=$(buildah from "${INPUT_BASE_IMAGE}")
    # Clean up the working container on exit (matters on self-hosted runners).
    # shellcheck disable=SC2064
    trap "buildah rm '$ctr' 2>/dev/null || true" EXIT

    # helper: run a command inside the container
    crun() { buildah run "$ctr" -- "$@"; }

    # ── package-manager detection ────────────────────────────────────
    case "$distro" in
        alpine)
            crun apk update
            crun apk upgrade
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun apk add "${packages[@]}"
            fi
            crun rm -rf /var/cache/apk/*
            ;;
        arch*)
            crun pacman -Syu --noconfirm
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun pacman -S --noconfirm "${packages[@]}"
            fi
            crun bash -c 'mkdir -p /var/cache/pacman/pkg && pacman -S --clean --noconfirm'
            ;;
        centos*)
            crun dnf upgrade -y --setopt=install_weak_deps=False
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun dnf install -y --setopt=install_weak_deps=False "${packages[@]}"
            fi
            crun dnf clean all
            ;;
        debian|ubuntu)
            crun bash -c "echo 'APT::Install-Recommends \"false\";' > /etc/apt/apt.conf.d/99-no-recommends"
            crun env DEBIAN_FRONTEND=noninteractive apt-get -qq update
            crun env DEBIAN_FRONTEND=noninteractive apt-get -qq -y dist-upgrade
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun env DEBIAN_FRONTEND=noninteractive apt-get -qq -y install "${packages[@]}"
            fi
            crun env DEBIAN_FRONTEND=noninteractive apt-get -qq clean
            ;;
        fedora)
            crun dnf upgrade -y --setopt=install_weak_deps=False
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun dnf install -y --setopt=install_weak_deps=False "${packages[@]}"
            fi
            crun dnf clean all
            ;;
        opensuse*)
            crun zypper update -y
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun zypper install -y "${packages[@]}"
            fi
            crun zypper clean
            ;;
        rocky)
            if [[ "$version" == 8* ]]; then
                repo="powertools"
            else
                repo="crb"
            fi
            crun dnf upgrade -y --setopt=install_weak_deps=False
            crun dnf install -y 'dnf-command(config-manager)'
            crun dnf config-manager --set-enabled "$repo"
            crun dnf install -y epel-release --setopt=install_weak_deps=False
            if [[ ${#packages[@]} -gt 0 ]]; then
                crun dnf install -y --setopt=install_weak_deps=False "${packages[@]}"
            fi
            crun dnf clean all
            ;;
        *)
            echo "::warning::Unknown distro '${distro}' -- skipping package installation"
            ;;
    esac
    endgroup

    # ── run custom commands ──────────────────────────────────────────
    if [[ -n "${INPUT_EXEC:-}" ]]; then
        group "Running custom commands (exec)"
        crun sh -c "${INPUT_EXEC}"
        endgroup
    fi

    # ── container config ─────────────────────────────────────────────
    workdir="${INPUT_WORKDIR:-/github/workspace}"
    buildah config --workingdir "$workdir" "$ctr"

    # ── commit & push ────────────────────────────────────────────────
    group "Committing and pushing image"
    # Use docker format for broad registry/client compatibility.
    # --squash collapses all layers so that package cache cleanup
    # actually reclaims space in the final image.
    buildah commit --squash --format docker "$ctr" "$image"
    buildah push --retry 3 "$image"
    endgroup

    echo "image=${image}" >> "$GITHUB_OUTPUT"
    echo "build-skipped=false" >> "$GITHUB_OUTPUT"
else
    echo "image=${image}" >> "$GITHUB_OUTPUT"
    echo "build-skipped=true" >> "$GITHUB_OUTPUT"
fi
