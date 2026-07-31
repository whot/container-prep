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
#   INPUT_REGISTRY       — container registry (default: ghcr.io, use 'containers-storage' for local)
#   INPUT_REGISTRY_USER  — registry login username
#   INPUT_TOKEN          — registry auth token
#   INPUT_EXEC           — shell commands to run after package installation
#   INPUT_WORKDIR        — working directory inside the container
#   INPUT_FORCE_REBUILD  — "true" to skip the cache check
#   INPUT_CHECK_ONLY     — "true" to only check, don't build
#   INPUT_USER_REPO      — for fork PRs: head repo full_name (e.g. "user/foo")
#   GITHUB_REPOSITORY    — owner/repo (the PR target / upstream repo)
#   GITHUB_OUTPUT        — file path for action outputs
#

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────
group()     { echo "::group::$1"; }
endgroup()  { echo "::endgroup::"; }
die()       { echo "::error::$*"; exit 1; }
msg() {
    local color="$1"
    shift
    local text="$*"
    local reset="\033[0m"
    local eol="\033[K"
    local rgb
    case "$color" in
        pink)
            rgb="239;177;246" # #efb1f6
            ;;
        blue)
            rgb="0;215;255" # #00d7ff
            ;;
        green)
            rgb="0;255;175" # #00ffaf
            ;;
        yellow)
            rgb="255;215;0" # #ffd700
            ;;
        *)
            die "Unsupported color '$color'"
            ;;
    esac

    local bg="\033[48;2;${rgb}m"
    local fg="\033[38;2;0;0;0m"
    echo -e "${bg}${fg}${text}${eol}${reset}"
}

DRY_RUN=""

function usage() {
    set +x
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Builds a container from the given base image."
    echo ""
    echo "Options:"
    echo "   --dry-run          Build images but do not commit to the registry"
    echo "   --verbose          Enable debugging output"
    echo "   --force            Force a rebuild even if image exists"
    echo ""
    echo "Project-specific options:"
    echo "   --tag              The image tag (required)"
    echo "   --base-image       The container base image"
    echo "   --packages         Space-separated list of packages to install"
    echo "   --suffix           Image suffix"
    echo "   --registry         Container registry to use (default: ghcr.io, use 'containers-storage' for local)"
    echo "   --user             GitHub registry user name"
    echo "   --token            GitHub personal access token value"
    echo "   --workdir          Working directory in the built container"
    echo "   --upstream-repo    Upstream repository project/name"
}

# ── local debugging ──────────────────────────────────────────────────────

# Fill in defaults for local debugging
if [[ -z "${CI:-}" ]]; then
    USER="${USER:-$(whoami)}"
    REPOSITORY="$(basename "$PWD")"
    INPUT_REGISTRY_USER="${INPUT_REGISTRY_USER:-$USER}"
    INPUT_USER_REPO="${INPUT_USER_REPO:-${USER}/${REPOSITORY}}"
    GITHUB_OUTPUT="${GITHUB_OUTPUT:-$(mktemp)}"

    SHORT="vh"
    LONG="help,verbose,dry-run,force,base-image:,tag:,packages:,suffix:,registry:,user:,token:,exec:,workdir:,upstream-repo:,"

    if ! ARGS=$(getopt -o "$SHORT" -l "$LONG" -- "$@"); then
        echo "Failed to parse options." >&2
        exit 1
    fi

    eval set -- "$ARGS"
    while true; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -v|--verbose)
                set -x
                shift
                ;;
            --dry-run)
                DRY_RUN="true"
                shift
                ;;
            --base-image)
                INPUT_BASE_IMAGE="$2"
                shift 2
                ;;
            --tag)
                INPUT_TAG="$2"
                shift 2
                ;;
            --packages)
                INPUT_PACKAGES="$2"
                shift 2
                ;;
            --suffix)
                INPUT_SUFFIX="$2"
                shift 2
                ;;
            --registry)
                INPUT_REGISTRY="$2"
                shift 2
                ;;
            --user)
                INPUT_REGISTRY_USER="$2"
                shift 2
                ;;
            --token)
                INPUT_TOKEN="$2"
                shift 2
                ;;
            --exec)
                # Special case: if the argument is a file, use
                # that file's content.
                if [[ -f "$2" ]]; then
                    INPUT_EXEC="$(cat "$2")"
                else
                    INPUT_EXEC="$2"
                fi
                shift 2
                ;;
            --workdir)
                INPUT_WORKDIR="$2"
                shift 2
                ;;
            --upstream-repo)
                GITHUB_REPOSITORY="$2"
                shift 2
                ;;
            --force)
                INPUT_FORCE_REBUILD="true"
                shift
                ;;
            --)
                shift
                break
                ;;
            *)
                echo "Unknown option ($1)" >&2
                exit 1
                ;;
        esac
    done

    if [[ -z "${INPUT_BASE_IMAGE:-}" ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        INPUT_BASE_IMAGE="${ID}:${VERSION_ID}"
        msg pink "Defaulting to base image '${INPUT_BASE_IMAGE}'"
    fi

    if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
        remotes=(origin github)
        for remote in "${remotes[@]}"; do
            url="$(git remote get-url "$remote" 2>/dev/null || true)"
            if [[ -n "$url" ]]; then
                # good enough to handle https:// and git@github.com
                url="${url##*:}"
                url="${url##*//}"
                project="$(basename "$(dirname "$url")")"
                repo="$(basename "$url" .git)"
                GITHUB_REPOSITORY="${project}/${repo}"
                break
            fi
        done
        if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
            msg pink "Defaulting to GitHub upstream repository '${GITHUB_REPOSITORY}'"
        fi
    fi
fi

INPUT_REGISTRY="${INPUT_REGISTRY:-ghcr.io}"
# A bit of special handling for debugging using local storage
if [[ "${INPUT_REGISTRY}" == "containers-storage" ]]; then
    SEP=":"
    TRANSPORT=""
    NEEDS_LOGIN="false"
else
    SEP="/"
    TRANSPORT="docker://"
    NEEDS_LOGIN="true"
fi

# ── prerequisite check ────────────────────────────────────────────────────
function check_required_env {
    local required=(
        "GITHUB_REPOSITORY"
        "GITHUB_OUTPUT"
        "INPUT_TAG"
    )
    local missing=()

    if [[ -z "$DRY_RUN" ]] && [[ "$NEEDS_LOGIN" != "false" ]]; then
        required+=("INPUT_TOKEN" "INPUT_REGISTRY_USER")
    fi

    set +u
    for var in "${required[@]}"; do
        if [[ -z "${!var}" ]]
        then
            missing+=( "$var" )
        fi
    done
    set -u

    if [[ ${#missing[@]} -gt 0 ]]
    then
        die "Missing environment variables: ${missing[*]}"
    fi
}

check_required_env

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
upstream_image="${INPUT_REGISTRY}${SEP}${GITHUB_REPOSITORY}/${suffix}:${INPUT_TAG}"

if [[ "$is_fork_pr" == "true" ]]; then
    user_image="${INPUT_REGISTRY}${SEP}${INPUT_USER_REPO}/${suffix}:${INPUT_TAG}"
    echo "Upstream image: $upstream_image"
    echo "User image:     $user_image"
else
    echo "Target image: $upstream_image"
fi

# ── registry login ───────────────────────────────────────────────────────
if [[ "${NEEDS_LOGIN}" == "true" ]]; then
    group "Registry login"
    echo "${INPUT_TOKEN:-}" | buildah login \
        --username "${INPUT_REGISTRY_USER}" \
        --password-stdin "${INPUT_REGISTRY}"
    endgroup
else
    msg yellow "Skipping registry login"
fi

# ── check whether image already exists ───────────────────────────────────
build_needed="true"
image=""       # will be set to the image reference we end up using

if [[ "${INPUT_FORCE_REBUILD:-false}" != "true" ]]; then
    group "Checking for existing image"

    # Step 1: check the upstream registry (always)
    echo "Checking upstream: $upstream_image"
    if [[ "$is_fork_pr" == "true" ]]; then
        # For fork PRs the token is scoped to the fork, not upstream.
        # Upstream is public so we can inspect without credentials.
        inspect_args=(--no-tags --retry-times 3)
    else
        inspect_args=(--no-tags --retry-times 3
                      --creds "${INPUT_REGISTRY_USER}:${INPUT_TOKEN:-}")
    fi

    if skopeo inspect "${inspect_args[@]}" \
            "${TRANSPORT}${upstream_image}" >/dev/null 2>&1; then
        msg green "Image ${upstream_image} already exists -- skipping build"
        image="$upstream_image"
        build_needed="false"
    elif [[ "$is_fork_pr" == "true" ]]; then
        # Step 2 (fork PRs only): check the user's registry
        msg yellow "Not found upstream -- checking user registry: $user_image"
        if skopeo inspect --no-tags --retry-times 3 \
                --creds "${INPUT_REGISTRY_USER}:${INPUT_TOKEN:-}" \
                "docker://${user_image}" >/dev/null 2>&1; then
            msg green "Image ${user_image} already exists -- skipping build"
            image="$user_image"
            build_needed="false"
        else
            msg yellow "Image not found in either registry -- will build"
            echo "::warning::Fork PR image not found. If you just" \
                 "pushed a tag change, your fork's CI may still be" \
                 "building the image. Re-run this workflow once your" \
                 "fork's build completes."
        fi
    else
        msg yellow "Image not found -- will build"
    fi

    endgroup

    if [[ "$build_needed" != "true" ]]; then
        msg green "Image ${image} already exists -- skipping build"
    fi
else
    msg yellow "Force-rebuild requested -- skipping cache check"
fi

# Set the target image for building / output.
# Always target the upstream image path.  For push events in a fork,
# GITHUB_REPOSITORY is the fork itself so this is the fork's registry.
# For pull_request events from a fork the GITHUB_TOKEN cannot write to
# either registry; the expectation is that the image was already built
# via a push event in the fork (see containers.yml pattern).
if [[ -z "$image" ]]; then
    image="$upstream_image"
fi

# ── check-only mode: output results and exit ─────────────────────────────
echo "build-needed=${build_needed}" >> "$GITHUB_OUTPUT"

if [[ "${INPUT_CHECK_ONLY:-false}" == "true" ]]; then
    echo "image=${image}" >> "$GITHUB_OUTPUT"
    echo "build-skipped=true" >> "$GITHUB_OUTPUT"
    echo "Check-only mode: build-needed=${build_needed}, image=${image}"
    exit 0
fi

# ── build if needed ──────────────────────────────────────────────────────
if [[ "$build_needed" == "true" ]]; then
    group "Building container"

    ctr=$(buildah from "${INPUT_BASE_IMAGE}")
    # Clean up the working container on exit (matters on self-hosted runners).
    # shellcheck disable=SC2064
    trap "buildah rm '$ctr' 2>/dev/null || true" EXIT

    # helper: run a command inside the container
    crun() {
        if [[ "${1:-}" != "-e" ]]; then
            buildah run "$ctr" -- "$@";
        else
            local -a env_args=()
            local -a positional_args=()

            while [[ $# -gt 0 ]]; do
                case "$1" in
                    -e)
                        [[ $# -ge 2 ]] || die "-e requires an argument"
                        env_args+=(-e "$2")
                        shift 2
                        ;;
                    --)
                        shift
                        positional_args+=("$@")
                        break
                        ;;
                    *)
                        positional_args+=("$1")
                        shift
                        ;;
                esac
            done

            [[ ${#positional_args[@]} -gt 0 ]] || die "crun: no command specified"
            buildah run "${env_args[@]}" "$ctr" -- "${positional_args[@]}"
        fi
    }

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
        # Allow pip to work without a virtual environment during exec
        crun -e "PIP_BREAK_SYSTEM_PACKAGES=1" sh -c "${INPUT_EXEC}"
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
    if [[ -z "$DRY_RUN" ]]; then
        if ! buildah push --retry 3 "$image"; then
            endgroup
            if [[ "$is_fork_pr" == "true" ]]; then
                die "Push failed. Fork PRs cannot push images" \
                     "to the upstream registry. Push the tag change to" \
                     "your fork first so that your fork's CI builds the" \
                     "image, then update this PR."
            fi
            die "Push to ${image} failed"
        fi
    else
        msg yellow "Not pushing image, this is a dry run"
    fi
    endgroup

    echo "image=${image}" >> "$GITHUB_OUTPUT"
    echo "build-skipped=false" >> "$GITHUB_OUTPUT"
else
    echo "image=${image}" >> "$GITHUB_OUTPUT"
    echo "build-skipped=true" >> "$GITHUB_OUTPUT"
fi
