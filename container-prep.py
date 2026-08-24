#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
#
# Usage: container-prep.py [OPTIONS]
#
# Examples:
#   $ container-prep.py from-scratch --distro fedora --distro-version 44 --tag foo
#   $ container-prep.py from-base --base-image ghcr.io/path/to/image:latest --tag foo
#   $ container-prep.py from-docker --dockerfile Dockerfile --tag foo
#   $ container-prep.py exists --distro fedora --distro-version 44 --tag foo
#
# Check whether a tagged container image exists in the registry;
# if not, build it from a base image and install packages.
#
# The image is identified by a distro, distro-version and tag tuple.
#
# Options:
#   --dry-run          Build images but do not commit to the registry
#   --verbose          Enable debugging output
#   --force            Force a rebuild even if image exists
#
# Project-specific options:
#   --registry        Container registry to use (default: ghcr.io, use 'containers-storage' for local)
#   --token           GitHub personal access token value
#   --upstream-repo   Upstream repository project/name
#   --user            GitHub registry user name
#   --user-repo       Fork PR: head repo full_name (e.g. 'user/foo')
#
# from-scratch Image building options:
#   --distro          The base image's distro name (e.g. 'fedora') (required)
#   --distro-version  The base image's distro version (e.g. '44') (required)
#   --distro-registry The base images's distro registry (e.g. 'quay.io/...")
#   --exec            Shell commands to run inside the container after package install
#   --packages        Space-separated list of packages to install
#   --platform        Target platform (e.g. linux/amd64, linux/arm64, linux/386)
#   --suffix          Image suffix
#   --tag             The image tag (required)
#   --workdir         Working directory in the built container
#
# from-base Image building options:
#   --base-image      Full path to a OCI-compatible base image (required)
#   --exec            Shell commands to run inside the container after package install
#   --packages        Space-separated list of packages to install
#   --platform        Target platform (e.g. linux/amd64, linux/arm64, linux/386)
#   --suffix          Image suffix
#   --tag             The image tag (required)
#   --workdir         Working directory in the built container
#
# from-docker Dockerfile-based options:
#   --distro          Optional image's distro name (e.g. 'fedora'), overrides
#                     the distribution extracted from the first FROM in the Dockerfile.
#   --distro-version  Optional image's distro version (e.g. '44'), overrides
#                     the version extracted from the first FROM in the Dockerfile
#   --distro-registry Optional images's distro registry (e.g. 'quay.io/..."), overrides
#                     the registry extracted from the first FROM in the Dockerfile.
#   --dockerfile      The name of the dockerfile to use
#   --platform        Target platform (e.g. linux/amd64, linux/arm64, linux/386),
#                     overrides the platform extracted from the first FROM line in
#                     the Dockerfile.
#   --tag             The image tag (required)
#
# exists checking options:
#   --distro          The base image's distro name (e.g. 'fedora') (required)
#   --distro-version  The base image's distro version (e.g. '44') (required)
#
# Environment variables:
#   GITHUB_OUTPUT        — file path for action outputs (set by GitHub Actions)
#

import enum
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from argparse import ArgumentParser, ArgumentTypeError
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NewType, Self

logger = logging.getLogger(__name__)

ImageTag = NewType("ImageTag", str)
ImageSuffix = NewType("ImageSuffix", str)
Platform = NewType("Platform", str)
DockerRegistry = NewType("DockerRegistry", str)
BaseImage = NewType("BaseImage", str)


@dataclass
class Credentials:
    user: str
    token: str


@dataclass
class Cmd:
    """Result of running a subprocess command."""

    args: list[str]
    returncode: int
    stdout: list[str]
    stderr: list[str]

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @classmethod
    def run(
        cls,
        cmd: list[str],
        input: str | None = None,
        stream: bool = False,
    ) -> Self:
        logger.debug(f"Running: {' '.join(cmd)}")
        if not stream:
            result = subprocess.run(
                cmd,
                input=input,
                text=True,
                capture_output=True,
                check=False,
            )
            stdout = result.stdout.splitlines() if result.stdout else []
            stderr = result.stderr.splitlines() if result.stderr else []
            for line in stdout:
                logger.debug(f"  stdout: {line}")
            for line in stderr:
                logger.debug(f"  stderr: {line}")
            return cls(
                args=cmd,
                returncode=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )

        # With streaming output we write stderr to stdout
        # because that's good enough for our test case
        stdout_lines = []
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if input:
            assert p.stdin is not None
            p.stdin.write(input)
            p.stdin.close()

        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip("\n")
            logger.debug(line)
            stdout_lines.append(line)

        p.wait()
        return cls(
            args=cmd,
            returncode=p.returncode,
            stdout=stdout_lines,
            stderr=[],
        )


class ToolNotFoundError(Exception):
    pass


class Tool:
    def __init__(self, toolname: str):
        self._toolname = toolname
        self._tool = None

    @property
    def tool(self) -> str:
        if not self._tool:
            self._tool = shutil.which(self._toolname)
        if not self._tool:
            raise ToolNotFoundError(f"{self._toolname} is required but not found")
        return self._tool


class Skopeo(Tool):
    """Wrapper around the skopeo commandline tool"""

    def __init__(self):
        super().__init__("skopeo")
        self.is_logged_in = False

    def inspect(self, image: str, creds: Credentials | None = None) -> Cmd:
        """Check whether an image exists in the registry."""
        args = [
            self.tool,
            "inspect",
            "--no-tags",
            "--retry-times=3",
        ]

        if creds:
            args.append(f"--creds={creds.user}:{creds.token}")

        args.append(image)
        return Cmd.run(args)

    def login(self, url: str, creds: Credentials) -> Cmd:
        """Login to a container registry."""
        assert self.tool is not None
        cmd = Cmd.run(
            [
                self.tool,
                "login",
                "--username",
                creds.user,
                "--password-stdin",
                url,
            ],
            input=creds.token,
        )
        self.is_logged_in = cmd.success
        return cmd


class Buildah(Tool):
    """Wrapper around the buildah commandline tool"""

    def __init__(self):
        super().__init__("buildah")

    def login(self, url: str, creds: Credentials) -> Cmd:
        """Login to a container registry."""
        assert self.tool is not None
        return Cmd.run(
            [
                self.tool,
                "login",
                "--username",
                creds.user,
                "--password-stdin",
                url,
            ],
            input=creds.token,
        )

    def bud(
        self,
        dockerfile: Path,
        tag: ImageTag,
        platform: Platform | None = None,
        squash: bool = False,
    ) -> Cmd:
        """Build an image from a Dockerfile."""
        assert self.tool is not None
        args = [self.tool, "bud", "--format", "docker"]
        if squash:
            args.append("--squash")
        if platform:
            args.extend(["--platform", platform])
        args.extend(["-f", str(dockerfile), "--tag", tag, "."])
        return Cmd.run(args, stream=True)

    def push(self, image: str, dest: str | None = None, retry: int = 3) -> Cmd:
        """Push an image to the registry."""
        assert self.tool is not None
        args = [self.tool, "push", f"--retry={retry}", image]
        if dest:
            args.append(dest)
        return Cmd.run(args)


@dataclass(frozen=True)
class Env:
    key: str
    value: str

    def __str__(self) -> str:
        return f"{self.key}={self.value}"


@dataclass(frozen=True)
class Distro:
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass(init=False, eq=True)
class GithubRepo:
    namespace: str = field(init=False)
    name: str = field(init=False)

    def __init__(self, path: str):
        self.namespace, self.name = path.rsplit("/", maxsplit=1)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"


class GithubLog:
    """Helper for GitHub Actions log commands.
    For local debugging this splits into a separate logger so it sticks out
    more, in the CI it just prints to stdout.
    """

    def __init__(self):
        self.logfunc = print
        self.is_ci = bool(os.environ.get("CI"))
        if not self.is_ci:
            self._logger = logging.getLogger("GitHub")
            self._logger.propagate = False
            self._logger.setLevel(logging.INFO)
            if not self._logger.handlers:
                handler = logging.StreamHandler()
                if sys.stderr.isatty():
                    green = "\033[1;32m"
                    reset = "\033[0m"
                else:
                    green = ""
                    reset = ""
                fmt = f"{green}GitHub  {reset}| %(message)s"
                handler.setFormatter(logging.Formatter(fmt))
                self._logger.addHandler(handler)
            self.logfunc = self._logger.info

    @contextmanager
    def group(self, title: str):
        """Context manager that wraps output in a GitHub Actions group."""
        self.logfunc(f"::group::{title}")
        try:
            yield
        finally:
            self.logfunc("::endgroup::")

    def warning(self, msg: str) -> None:
        self.logfunc(f"::warning::{msg}")

    def error(self, msg: str) -> None:
        self.logfunc(f"::error::{msg}")


@dataclass
class GithubOutput:
    """Helper for printing to the GITHUB_OUTPUT file"""

    _outputs: list[tuple[str, str]] = field(default_factory=list)

    def add(self, key: str, value: str) -> Self:
        self._outputs.append((key, value))
        return self

    def __str__(self) -> str:
        return "\n".join(f"{key}={value}" for key, value in self._outputs)

    def write(self) -> None:
        """Append outputs to the file specified by GITHUB_OUTPUT."""
        output_file = os.getenv("GITHUB_OUTPUT")
        if not output_file:
            logger.warning("GITHUB_OUTPUT not set, skipping output write")
            return
        with open(output_file, "a") as f:
            f.write(str(self) + "\n")


class DockerInstruction(enum.Enum):
    FROM = "FROM"
    RUN = "RUN"
    ENV = "ENV"
    WORKDIR = "WORKDIR"
    COPY = "COPY"
    COMMENT = "#"


@dataclass(frozen=True)
class DockerCommand:
    type: DockerInstruction
    cmd: str

    @dataclass
    class Mount:
        source: Path
        target: Path | None = None
        """The target in the container (defaults to source)"""
        type: Literal["bind", "cache", "tmpfs", "secret", "ssh"] = "bind"

        def __str__(self) -> str:
            parts = [f"type={self.type}", f"source={self.source}"]
            parts.append(f"target={self.target or self.source}")
            return f"--mount={','.join(parts)}"

    def __str__(self) -> str:
        return f"{self.type.value} {self.cmd}"

    @classmethod
    def run(
        cls,
        cmd: str,
        mounts: list["DockerCommand.Mount"] | None = None,
        env: list[Env] | None = None,
    ) -> Self:
        # Prepend inline env vars to the command itself
        if env:
            env_prefix = " ".join(str(e) for e in env)
            cmd = f"{env_prefix} {cmd}"
        if not mounts:
            return cls(type=DockerInstruction.RUN, cmd=cmd)
        mount_prefix = " \\\n    ".join(str(m) for m in mounts)
        return cls(type=DockerInstruction.RUN, cmd=f"{mount_prefix} \\\n    {cmd}")

    @classmethod
    def from_distro(
        cls,
        distro: Distro,
        registry: DockerRegistry | None,
        platform: str | None = None,
    ) -> Self:
        """Create a FROM instruction"""
        p = f"--platform={platform} " if platform else ""
        r = f"{registry}/" if registry else ""
        from_ = f"{p}{r}{distro}"
        return cls(type=DockerInstruction.FROM, cmd=from_)

    @classmethod
    def from_base(
        cls,
        base_image: BaseImage,
        platform: str | None = None,
    ) -> Self:
        """Create a FROM instruction"""
        p = f"--platform={platform} " if platform else ""
        from_ = f"{p}{base_image}"
        return cls(type=DockerInstruction.FROM, cmd=from_)

    @classmethod
    def env(cls, key: str, value: str) -> Self:
        e = Env(key=key, value=value)
        return cls(type=DockerInstruction.ENV, cmd=str(e))

    @classmethod
    def workdir(cls, path: str) -> Self:
        return cls(type=DockerInstruction.WORKDIR, cmd=path)

    @classmethod
    def copy(cls, src: str, dst: str) -> Self:
        return cls(type=DockerInstruction.COPY, cmd=f"{src} {dst}")

    @classmethod
    def comment(cls, text: str) -> Self:
        return cls(type=DockerInstruction.COMMENT, cmd=text)


@dataclass
class DockerDistro:
    """Wrapper around the distro selection in a FROM line of a Dockerfile"""

    distro: Distro
    platform: Platform | None
    registry_name: DockerRegistry | None

    def __str__(self) -> str:
        platform = f"--platform={self.platform} " if self.platform else ""
        registry = f"{self.registry_name}/" if self.registry_name else ""
        return f"{platform}{registry}{self.distro}"


@dataclass
class Dockerfile:
    cmds: list[DockerCommand] = field(default_factory=list)
    _distro: DockerDistro | None = None
    _needs_regenerate: bool = False
    _generated_files: list[str] = field(default_factory=list)

    def add(self, cmd: DockerCommand) -> Self:
        self.cmds.append(cmd)
        return self

    def _regenerate(self):
        if not self._needs_regenerate:
            return

        updated = False

        def update_from(fro: DockerCommand) -> DockerCommand:
            nonlocal updated
            if updated:
                return fro
            updated = True
            return DockerCommand(
                type=fro.type,
                cmd=str(self.distro),
            )

        self.cmds = [
            update_from(cmd) if cmd.type == DockerInstruction.FROM else cmd
            for cmd in self.cmds
        ]
        self._needs_regenerate = False

    def __str__(self) -> str:
        self._regenerate()
        return "\n".join(str(cmd) for cmd in self.cmds) + "\n"

    def override_distro(self, distro: Distro) -> Self:
        _ = self.distro
        if self._distro is None:
            self._distro = DockerDistro(distro, platform=None, registry_name=None)
        else:
            self._distro.distro = distro
        self._needs_regenerate = True
        return self

    def override_platform(self, platform: Platform | None) -> Self:
        _ = self.distro
        # Not worth fixing, just make sure a distro is set first
        if self._distro is None:
            raise ValueError("Cannot override platform without a distro")
        self._distro.platform = platform
        self._needs_regenerate = True
        return self

    def override_registry(self, registry_name: DockerRegistry | None) -> Self:
        _ = self.distro
        # Not worth fixing, just make sure a distro is set first
        if self._distro is None:
            raise ValueError("Cannot override registry name without a distro")
        self._distro.registry_name = registry_name
        self._needs_regenerate = True
        return self

    def _extract_distro(self):
        """Extract distro info from the first FROM instruction.

        Returns None if no FROM instruction exists.
        """
        for cmd in self.cmds:
            if cmd.type != DockerInstruction.FROM:
                continue

            # Parse: [--platform=<platform>] <image>
            tokens = cmd.cmd.split()
            platform = None
            image = None
            for token in tokens:
                if token.startswith("--platform="):
                    platform = Platform(token.split("=", 1)[1])
                elif not token.upper().startswith("AS"):
                    image = token
                    break

            if not image:
                return

            # Strip registry prefix: "registry.fedoraproject.org/fedora:44" → "fedora:44"
            if "/" in image:
                registry_name = DockerRegistry(image.rsplit("/", 1)[0])
                short = image.split("/")[-1]
            else:
                registry_name = None
                short = image

            if ":" not in short:
                return

            distro_name, version = short.split(":", 1)
            if not distro_name or not version:
                return

            self._distro = DockerDistro(
                distro=Distro(distro_name, version),
                platform=platform,
                registry_name=registry_name,
            )

    @property
    def distro(self) -> DockerDistro | None:
        if not self._distro:
            self._extract_distro()
        return self._distro

    @classmethod
    def parse(cls, path: Path) -> Self:
        """Parse a Dockerfile into a list of DockerCommands.

        Only parses FROM lines fully; all other lines are preserved
        as raw commands of their respective type.
        """
        if not path.exists():
            raise FileNotFoundError(f"Dockerfile '{path}' not found")

        dockerfile = cls()
        lines = path.read_text().splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and comments
            if not line:
                i += 1
                continue
            if line.startswith("#"):
                dockerfile.add(DockerCommand.comment(line[1:].strip()))
                i += 1
                continue

            # Handle line continuations
            while line.endswith("\\") and i + 1 < len(lines):
                i += 1
                line = line[:-1] + "\\\n    " + lines[i].strip()

            # Parse the instruction
            parts = line.split(None, 1)
            instruction = parts[0].upper()
            arg = parts[1] if len(parts) > 1 else ""

            try:
                dtype = DockerInstruction(instruction)
            except ValueError:
                # Unknown instruction, preserve as-is via RUN
                dockerfile.add(DockerCommand(type=DockerInstruction.RUN, cmd=line))
                i += 1
                continue

            dockerfile.add(DockerCommand(type=dtype, cmd=arg))
            i += 1

        return dockerfile

    def add_install_packages(self, pm: "PackageManager", packages: list[str]):
        for cmd in pm.docker_commands(packages):
            self.add(cmd)

    def add_exec_script(self, exec_cmd: str):
        # Write env file
        env_file = Path(".container-prep-env")
        with open(env_file, "w") as f:
            for key, value in os.environ.items():
                if key == "PATH":
                    continue
                f.write(f"export {key}={shlex.quote(value)}\n")
        self._generated_files.append(str(env_file))

        # Write exec script
        exec_script = Path(".container-prep-exec")
        exec_script.write_text(exec_cmd + "\n")
        exec_script.chmod(0o755)
        self._generated_files.append(str(exec_script))

        mounts = [
            DockerCommand.Mount(source=env_file, target=Path("/tmp/.env")),
            DockerCommand.Mount(source=exec_script, target=Path("/tmp/exec.sh")),
        ]

        # If we have .git repo in GITHUB_WORKSPACE, mount it during exec
        repo_dir = Path(os.environ.get("GITHUB_WORKSPACE", "."))
        if (repo_dir / ".git").is_dir():
            mounts.append(
                DockerCommand.Mount(source=Path("."), target=Path("/tmp/clone"))
            )
            cd_to_clone = "cd /tmp/clone && "
        else:
            cd_to_clone = ""

        run_cmd = (
            f"{cd_to_clone}. /tmp/.env "
            "&& set -eux "
            "&& PIP_BREAK_SYSTEM_PACKAGES=1 sh /tmp/exec.sh"
        )
        self.add(DockerCommand.run(run_cmd, mounts=mounts))

    def __del__(self):
        for file in self._generated_files:
            Path(file).unlink()


@dataclass
class PackageManager:
    """Distro-specific package manager commands for Dockerfile generation.

    Each field is a list of shell commands to be &&-chained in a single RUN.
    """

    upgrade: list[str]
    install: list[str]
    clean: list[str]
    setup: list[str] = field(default_factory=list)
    """Commands to run before anything else (e.g. apt config). These
    become separate Dockerfile instructions (RUN)."""
    envs: list[Env] = field(default_factory=list)
    """Environment variables required by this package manager
    (emitted as ENV instructions before the main RUN)."""

    @classmethod
    def get(cls, distro: Distro) -> Self:
        """Return the PackageManager for a given distro name.

        Raises ValueError for unknown distros.
        """
        if distro.name == "alpine":
            return cls(
                upgrade=["apk update", "apk upgrade"],
                install=["apk add"],
                clean=["rm -rf /var/cache/apk/*"],
            )
        elif distro.name.startswith("arch"):
            return cls(
                upgrade=["pacman -Syu --noconfirm"],
                install=["pacman -S --noconfirm"],
                clean=[
                    "mkdir -p /var/cache/pacman/pkg",
                    "pacman -S --clean --noconfirm",
                ],
            )
        elif distro.name.startswith("centos"):
            return cls(
                upgrade=["dnf upgrade -y --setopt=install_weak_deps=False"],
                install=["dnf install -y --setopt=install_weak_deps=False"],
                clean=["dnf clean all"],
            )
        elif distro.name in ("debian", "ubuntu"):
            return cls(
                setup=[
                    (
                        "echo 'APT::Install-Recommends \"false\";' "
                        "> /etc/apt/apt.conf.d/99-no-recommends"
                    )
                ],
                envs=[Env("DEBIAN_FRONTEND", "noninteractive")],
                upgrade=["apt-get -qq update", "apt-get -qq -y dist-upgrade"],
                install=["apt-get -qq -y install"],
                clean=["apt-get -qq clean"],
            )
        elif distro.name == "fedora":
            return cls(
                upgrade=["dnf upgrade -y --setopt=install_weak_deps=False"],
                install=["dnf install -y --setopt=install_weak_deps=False"],
                clean=["dnf clean all"],
            )
        elif distro.name.startswith("opensuse"):
            return cls(
                upgrade=["zypper update -y"],
                install=["zypper install -y"],
                clean=["zypper clean"],
            )
        elif distro.name == "rocky":
            repo = "powertools" if distro.version.startswith("8") else "crb"
            return cls(
                upgrade=[
                    "dnf upgrade -y --setopt=install_weak_deps=False",
                    "dnf install -y 'dnf-command(config-manager)'",
                    f"dnf config-manager --set-enabled {repo}",
                    "dnf install -y epel-release --setopt=install_weak_deps=False",
                ],
                install=["dnf install -y --setopt=install_weak_deps=False"],
                clean=["dnf clean all"],
            )
        else:
            raise ValueError(f"Unknown distro '{distro}'")

    def required_envs(self) -> list[str]:
        """Return the list of environment variable names required by this
        package manager."""
        return [e.key for e in self.envs]

    def docker_commands(self, packages: list[str] | None = None) -> list[DockerCommand]:
        """Generate DockerCommand instructions for this package manager.

        Returns a list of DockerCommands: optional setup RUN/ENV instructions,
        then a single RUN with upgrade + install + clean chained with &&.
        """
        cmds: list[DockerCommand] = []

        # Setup commands (separate RUN instructions)
        for setup_cmd in self.setup:
            cmds.append(DockerCommand.run(setup_cmd))

        # ENV instructions
        for env in self.envs:
            cmds.append(DockerCommand.env(env.key, env.value))

        # Main RUN: upgrade && [install packages &&] clean
        run_parts = list(self.upgrade)
        if packages:
            # install command is e.g. ["dnf install -y ..."], append packages
            for install_cmd in self.install:
                run_parts.append(f"{install_cmd} {' '.join(packages)}")
        run_parts.extend(self.clean)

        cmds.append(DockerCommand.run(" \\\n    && ".join(run_parts)))
        return cmds


@dataclass
class ImageId:
    """A single image's ID, usually distro/version:tag.

    An ImageId does not include the registry and it does not include
    the GithubRepo it is supposed to be pushed to, eventually.
    """

    image_id: str
    tag: ImageTag

    def __str__(self):
        return self.image_id

    @classmethod
    def create(
        cls,
        distro: Distro,
        tag: ImageTag,
        suffix: ImageSuffix | None = None,
        platform: Platform | None = None,
    ) -> Self:
        if not suffix and platform:
            arch = platform.split("/")[-1]
            sfx = f"{distro.name}/{distro.version}/{arch}"
        elif not suffix:
            sfx = f"{distro.name}/{distro.version}"
        else:
            sfx = str(suffix)

        image_id = f"{sfx}:{tag}"
        return cls(image_id=image_id, tag=tag)

    @classmethod
    def create_with_suffix(
        cls,
        tag: ImageTag,
        suffix: ImageSuffix,
        platform: Platform | None = None,
    ) -> Self:
        if not suffix and platform:
            arch = platform.split("/")[-1]
            sfx = f"{suffix}/{arch}"
        else:
            sfx = str(suffix)

        image_id = f"{sfx}:{tag}"
        return cls(image_id=image_id, tag=tag)


@dataclass
class ImagePath:
    """
    A full image path to be passed to a registry. This is
    an ImageId combined with a GithubRepo's path, e.g.
    user/blah/fedora/44:latest
    """

    path: str
    _image_id: ImageId

    def __str__(self):
        return self.path

    @property
    def tag(self) -> ImageTag:
        return self._image_id.tag

    @classmethod
    def create(
        cls,
        repo: GithubRepo,
        image_id: ImageId,
    ) -> Self:
        """Compute the image path reference for a given repo"""
        path = f"{repo}/{image_id}"
        return cls(path, _image_id=image_id)


@dataclass
class RegistryImage:
    """Wrapper around an image in a registry (whether it exists or not)."""

    exists: bool
    image_path: ImagePath
    _full_path: str
    _registry: "Registry"

    @property
    def url(self) -> str:
        """Full transport URL for skopeo (e.g. docker://ghcr.io/owner/repo/distro/ver:tag)."""
        return self._full_path

    @property
    def local_name(self) -> str:
        """Registry-qualified name for buildah (e.g. ghcr.io/owner/repo/distro/ver:tag).

        For containers-storage, prefixes with localhost/."""
        if self._registry.is_local:
            return f"localhost/{self.image_path}"
        return f"{self._registry.name}/{self.image_path}"

    @property
    def tag(self) -> ImageTag:
        return self.image_path.tag

    def __str__(self) -> str:
        return self.url


@dataclass
class Registry:
    """A container registry, typically ghcr.io with associated credentials."""

    name: str
    creds: Credentials | None
    _skopeo: Skopeo = field(init=False, default_factory=Skopeo)
    _buildah: Buildah = field(init=False, default_factory=Buildah)

    @property
    def url(self) -> str:
        if self.name == "containers-storage":
            return f"{self.name}:"
        elif not self.name.startswith("docker://"):
            return f"docker://{self.name}/"
        return f"{self.name}/"

    @property
    def is_local(self) -> bool:
        return self.name == "containers-storage"

    @classmethod
    def by_name(cls, name: str, creds: Credentials | None) -> Self:
        return cls(name, creds=creds)

    @classmethod
    def default(cls, creds: Credentials | None) -> Self:
        return cls.by_name(name="ghcr.io", creds=creds)

    def login(self) -> bool:
        if not self.creds:
            return True
        if self.is_local:
            logger.debug("Skipping registry login for local registry")
            return True

        cmd = self._skopeo.login(self.name, self.creds)
        return cmd.success

    def check_image(self, image_path: ImagePath) -> RegistryImage:
        if not self._skopeo.is_logged_in:
            self.login()

        full_path = f"{self.url}{image_path}"
        cmd = self._skopeo.inspect(image=full_path, creds=self.creds)
        return RegistryImage(
            exists=cmd.success,
            image_path=image_path,
            _full_path=full_path,
            _registry=self,
        )

    def push(self, image: RegistryImage) -> Cmd:
        if not self._skopeo.is_logged_in:
            self.login()

        # Use the registry-qualified local name that buildah bud
        # tagged the image with (e.g. ghcr.io/owner/repo/distro/ver:tag
        # or localhost/owner/repo/distro/ver:tag for local storage).
        return self._buildah.push(image.local_name)


def images_exist(
    registry: Registry,
    upstream_repo: GithubRepo,
    user_repo: GithubRepo | None,
    image_id: ImageId,
) -> RegistryImage:
    """Check if a image exists in either of the given repos, returning
    the RegistryImage that does exist, or the nonexisting Registry
    image for the upstream image."""
    ghlog = GithubLog()

    is_fork_pr = user_repo and user_repo != upstream_repo

    upstream_image = ImagePath.create(
        repo=upstream_repo,
        image_id=image_id,
    )

    if is_fork_pr and user_repo:
        user_image = ImagePath.create(
            repo=user_repo,
            image_id=image_id,
        )
        logger.info(f"Upstream image: {upstream_image.path}")
        logger.info(f"User image:     {user_image.path}")
    else:
        user_image = None
        logger.info(f"Target image: {upstream_image.path}")

    with ghlog.group("Checking for existing image"):
        logger.info(f"Checking upstream: {upstream_image.path}")

        registry.login()
        upstream_img = registry.check_image(upstream_image)
        if upstream_img.exists:
            logger.info(f"Image {upstream_image.path} exists")
            return upstream_img

        if is_fork_pr and user_image is not None:
            logger.info(
                f"Not found upstream -- checking user registry: {user_image.path}"
            )
            user_img = registry.check_image(user_image)
            if user_img.exists:
                logger.info(f"Image {user_image.path} already exists -- skipping build")
                return user_img

            logger.info("Image not found in either registry")
            warn = True
        else:
            logger.info("Image not found")
            warn = False

    # We want this outside the ghlog.group
    if warn:
        ghlog.warning(
            "Fork PR image not found. If you just pushed a tag change, "
            "your fork's CI may still be building the image. Re-run "
            "this workflow once your fork's build completes."
        )

    return upstream_img


def registry_from_args(args):
    creds = None
    if args.token and args.user:
        creds = Credentials(user=args.user, token=args.token)
    registry = (
        Registry(args.registry, creds=creds)
        if args.registry
        else Registry.default(creds=creds)
    )
    return registry


def build_and_push(
    registry: Registry,
    dockerfile: Dockerfile,
    image: RegistryImage,
    dry_run: bool = False,
) -> bool:
    """Build from a Dockerfile and push to the registry.

    Returns True on success, False on failure.
    """
    ghlog = GithubLog()
    buildah = Buildah()
    output = GithubOutput()

    with ghlog.group("Registry login"):
        if not registry.login():
            ghlog.error("Registry login failed")
            return False

    dockerfile_path = None
    try:
        with ghlog.group("Writing Dockerfile"):
            content = str(dockerfile)
            for line in content.split("\n"):
                logger.debug(line)
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix="container-prep-",
                suffix=".Dockerfile",
                delete=False,
            ) as f:
                f.write(content)
                dockerfile_path = Path(f.name)

        with ghlog.group("Building container"):
            # Tag with the registry-qualified name so buildah push
            # can find and push it without extra arguments.
            result = buildah.bud(
                dockerfile=dockerfile_path,
                tag=ImageTag(image.local_name),
            )
            if not result.success:
                ghlog.error("Build failed")
                return False
    finally:
        if dockerfile_path:
            dockerfile_path.unlink()

    with ghlog.group("Pushing image"):
        if dry_run:
            logger.info("Not pushing image, this is a dry run")
        else:
            result = registry.push(image)
            if result.returncode != 0:
                ghlog.error(f"Push to {image} failed")
                return False

    output.add("image", image.local_name)
    output.add("build-skipped", "false")
    output.write()
    return True


def cmd_from_scratch(args) -> int:
    """Build a container image from a base image with packages.

    Returns 0 on success, 1 on failure.
    """
    ghlog = GithubLog()

    registry = registry_from_args(args)
    upstream_repo = GithubRepo(args.upstream_repo)
    user_repo = GithubRepo(args.user_repo) if args.user_repo else None
    distro = Distro(args.distro, args.distro_version)
    distro_registry = args.distro_registry
    platform = args.platform

    image_id = ImageId.create(
        distro=distro,
        tag=ImageTag(args.tag),
        suffix=ImageSuffix(args.suffix) if args.suffix else None,
        platform=args.platform if args.platform else None,
    )
    logger.info(f"Image Id: {image_id.image_id}")

    image = images_exist(
        registry=registry,
        upstream_repo=upstream_repo,
        user_repo=user_repo,
        image_id=image_id,
    )
    if image.exists:
        if not args.force:
            output = GithubOutput()
            output.add("image", image.local_name)
            output.add("build-skipped", "true")
            output.write()
            return 0
        ghlog.warning("Image exists but force-rebuilding it")

    dockerfile = Dockerfile()
    dockerfile.add(
        DockerCommand.from_distro(
            distro=distro, registry=distro_registry, platform=platform
        )
    )

    packages = args.packages.split() if args.packages else None
    if packages:
        try:
            pm = PackageManager.get(distro)
            dockerfile.add_install_packages(pm, packages)
        except ValueError:
            ghlog.warning(f"Unknown distro '{distro}' -- skipping package installation")
            pm = None

    if args.exec_cmd:
        dockerfile.add_exec_script(args.exec_cmd)

    dockerfile.add(DockerCommand.workdir(args.workdir))

    success = build_and_push(
        registry=registry,
        dockerfile=dockerfile,
        image=image,
        dry_run=args.dry_run,
    )
    if not success:
        user_repo = getattr(args, "user_repo", None)
        is_fork_pr = user_repo and user_repo != args.upstream_repo
        if is_fork_pr:
            ghlog.error(
                "Push failed. Fork PRs cannot push images "
                "to the upstream registry. Push the tag change to "
                "your fork first so that your fork's CI builds the "
                "image, then update this PR."
            )
    return 0 if success else 1


def cmd_from_base(args) -> int:
    """Build a container image from a base image with packages.

    Returns 0 on success, 1 on failure.
    """
    ghlog = GithubLog()

    registry = registry_from_args(args)
    upstream_repo = GithubRepo(args.upstream_repo)
    user_repo = GithubRepo(args.user_repo) if args.user_repo else None
    base = BaseImage(args.base_image)
    platform = args.platform

    image_id = ImageId.create_with_suffix(
        tag=ImageTag(args.tag),
        suffix=ImageSuffix(args.suffix),
        platform=args.platform if args.platform else None,
    )
    logger.info(f"Image Id: {image_id.image_id}")

    image = images_exist(
        registry=registry,
        upstream_repo=upstream_repo,
        user_repo=user_repo,
        image_id=image_id,
    )
    if image.exists:
        if not args.force:
            output = GithubOutput()
            output.add("image", image.local_name)
            output.add("build-skipped", "true")
            output.write()
            return 0
        ghlog.warning("Image exists but force-rebuilding it")

    dockerfile = Dockerfile()
    dockerfile.add(DockerCommand.from_base(base_image=base, platform=platform))

    packages = args.packages.split() if args.packages else None
    if packages:
        if args.distro and args.distro_version:
            distro = Distro(args.distro, args.distro_version)
            try:
                pm = PackageManager.get(distro)
                dockerfile.add_install_packages(pm, packages)
            except ValueError:
                ghlog.warning(
                    f"Unknown distro '{distro}' -- skipping package installation"
                )
                pm = None
        else:
            distro = Distro(args.distro, args.distro_version)
            ghlog.warning("Unknown distro -- skipping package installation")

    if args.exec_cmd:
        dockerfile.add_exec_script(args.exec_cmd)

    dockerfile.add(DockerCommand.workdir(args.workdir))

    success = build_and_push(
        registry=registry,
        dockerfile=dockerfile,
        image=image,
        dry_run=args.dry_run,
    )
    if not success:
        user_repo = getattr(args, "user_repo", None)
        is_fork_pr = user_repo and user_repo != args.upstream_repo
        if is_fork_pr:
            ghlog.error(
                "Push failed. Fork PRs cannot push images "
                "to the upstream registry. Push the tag change to "
                "your fork first so that your fork's CI builds the "
                "image, then update this PR."
            )
    return 0 if success else 1


def cmd_from_docker(args) -> int:
    """Build a container image from a user-provided Dockerfile.

    Returns 0 on success, 1 on failure.
    """
    ghlog = GithubLog()

    registry = registry_from_args(args)

    dockerfile_path = Path(args.dockerfile)
    if not dockerfile_path.exists():
        ghlog.error(f"Dockerfile '{args.dockerfile}' not found")
        return 1

    dockerfile = Dockerfile.parse(dockerfile_path)
    docker_distro = dockerfile.distro
    if not docker_distro:
        ghlog.error(
            f"Failed to parse distro/version from FROM line in '{args.dockerfile}'. "
            "The FROM image must include a version tag (e.g. 'fedora:44')"
        )
        return 1

    # args overrides FROM so dockerfiles are more re-usable
    d = args.distro
    v = args.distro_version
    if d or v:
        dockerfile.override_distro(
            Distro(
                name=d if d else docker_distro.distro.name,
                version=v if v else docker_distro.distro.version,
            )
        )
    if p := args.platform:
        dockerfile.override_platform(p)
    if r := args.distro_registry:
        dockerfile.override_registry(r)

    logger.info(f"Dockerfile: {args.dockerfile}")
    logger.info(f"Dockerfile FROM: {dockerfile.distro}")

    upstream_repo = GithubRepo(args.upstream_repo)
    user_repo = GithubRepo(args.user_repo) if args.user_repo else None
    dockerfile_distro = dockerfile.distro
    assert dockerfile_distro is not None
    image_id = ImageId.create(
        distro=dockerfile_distro.distro,
        tag=ImageTag(args.tag),
        suffix=ImageSuffix(args.suffix) if args.suffix else None,
        platform=args.platform if args.platform else docker_distro.platform,
    )
    logger.info(f"Image Id: {image_id.image_id}")

    image = images_exist(
        registry=registry,
        upstream_repo=upstream_repo,
        user_repo=user_repo,
        image_id=image_id,
    )
    if image.exists:
        if not args.force:
            output = GithubOutput()
            output.add("image", image.local_name)
            output.add("build-skipped", "true")
            output.write()
            return 0

        ghlog.warning("Image exists but force-rebuilding it")

    success = build_and_push(
        registry,
        dockerfile=dockerfile,
        image=image,
        dry_run=args.dry_run,
    )
    if not success:
        user_repo = getattr(args, "user_repo", None)
        is_fork_pr = user_repo and user_repo != args.upstream_repo
        if is_fork_pr:
            ghlog.error(
                "Push failed. Fork PRs cannot push images "
                "to the upstream registry. Push the tag change to "
                "your fork first so that your fork's CI builds the "
                "image, then update this PR."
            )
    return 0 if success else 1


def cmd_exists(args) -> int:
    """Invoked when the user runs container-prep exists [OPTIONS]"""
    registry = registry_from_args(args)
    repo = GithubRepo(args.upstream_repo)
    user_repo = GithubRepo(args.user_repo) if args.user_repo else None

    distro = Distro(args.distro, args.distro_version)
    image_id = ImageId.create(
        distro=distro,
        tag=ImageTag(args.tag),
        suffix=ImageSuffix(args.suffix) if args.suffix else None,
        platform=Platform(args.platform) if args.platform else None,
    )

    image = images_exist(
        registry=registry,
        upstream_repo=repo,
        user_repo=user_repo,
        image_id=image_id,
    )
    return 0 if image.exists else 1


def main():
    parser = ArgumentParser(
        description="Check whether a tagged container image exists in the registry; "
        "if not, build it from a base image and install packages.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for info, -vv for debug)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_project_options(sub: ArgumentParser) -> None:
        group = sub.add_argument_group("project-specific options")

        def validate_tag(value: str) -> str:
            if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.\-]{0,127}", value):
                raise ArgumentTypeError(
                    f"'{value}' is not a valid OCI tag "
                    "(must match [a-zA-Z0-9_.-]{1,128}, cannot start with '.' or '-')"
                )
            return value

        group.add_argument(
            "--tag", required=True, type=validate_tag, help="The image tag (required)"
        )
        group.add_argument(
            "--registry",
            default="ghcr.io",
            help="Container registry to use (default: ghcr.io, "
            "use 'containers-storage' for local)",
        )
        group.add_argument(
            "--token", default=None, help="GitHub personal access token value"
        )
        group.add_argument(
            "--upstream-repo",
            default=os.environ.get("GITHUB_REPOSITORY"),
            help="Upstream repository project/name",
        )
        group.add_argument("--user", default=None, help="GitHub registry user name")
        group.add_argument(
            "--user-repo",
            default=None,
            help="Fork PR: head repo full_name (e.g. 'user/foo')",
        )

        group.add_argument(
            "--platform",
            default=None,
            type=Platform,
            help="Target platform (e.g. linux/amd64, linux/arm64, linux/386)",
        )

    def add_build_options(sub: ArgumentParser) -> None:
        sub.add_argument(
            "--dry-run",
            action="store_true",
            help="Build images but do not commit to the registry",
        )
        sub.add_argument(
            "--force", action="store_true", help="Force a rebuild even if image exists"
        )

    scratch_parser = subparsers.add_parser(
        "from-scratch",
        help="Build a container image from a base image with packages",
    )
    add_build_options(scratch_parser)
    add_project_options(scratch_parser)

    scratch_group = scratch_parser.add_argument_group("image building options")
    scratch_group.add_argument(
        "--distro",
        required=True,
        help="The base image's distro name (e.g. 'fedora')",
    )
    scratch_group.add_argument(
        "--distro-version",
        required=True,
        help="The base image's distro version (e.g. '44')",
    )
    scratch_group.add_argument(
        "--packages",
        default=None,
        help="Space-separated list of packages to install",
    )
    scratch_group.add_argument(
        "--distro-registry",
        default=None,
        type=DockerRegistry,
        help="Container registry for the base image (default: None)",
    )
    scratch_group.add_argument(
        "--exec",
        default=None,
        dest="exec_cmd",
        help="Shell commands to run inside the container after package install",
    )
    scratch_group.add_argument(
        "--workdir",
        default="/github/workspace",
        help="Working directory in the built container (default: /github/workspace)",
    )
    scratch_group.add_argument("--suffix", default=None, help="Image suffix")

    base_parser = subparsers.add_parser(
        "from-base",
        help="Build a container image from a base image",
    )
    add_build_options(base_parser)
    add_project_options(base_parser)

    base_group = base_parser.add_argument_group("image building options")
    base_group.add_argument(
        "--base-image",
        required=True,
        help="The base image's OCI-compatible URI",
    )
    base_group.add_argument("--suffix", required=True, help="Image suffix")
    base_group.add_argument(
        "--distro",
        default=None,
        help="Optional distro name, used to detect the package manager for package installs",
    )
    base_group.add_argument(
        "--distro-version",
        default=None,
        help="Optional distro version, used to detect the package manager for package installs",
    )
    base_group.add_argument(
        "--packages",
        default=None,
        help="Space-separated list of packages to install",
    )
    base_group.add_argument(
        "--exec",
        default=None,
        dest="exec_cmd",
        help="Shell commands to run inside the container after package install",
    )
    base_group.add_argument(
        "--workdir",
        default="/github/workspace",
        help="Working directory in the built container (default: /github/workspace)",
    )

    docker_parser = subparsers.add_parser(
        "from-docker",
        help="Build a container image from a Dockerfile",
    )
    add_build_options(docker_parser)
    add_project_options(docker_parser)

    docker_group = docker_parser.add_argument_group("dockerfile options")
    docker_group.add_argument(
        "--dockerfile", required=True, help="The name of the dockerfile to use"
    )
    docker_group.add_argument(
        "--distro",
        default=None,
        help="Optional distro name, overrides the "
        "distribution extracted from the first FROM "
        "in the Dockerfile",
    )
    docker_group.add_argument(
        "--distro-version",
        default=None,
        help="Optional distro version, overrides the "
        "version extracted from the first FROM "
        "in the Dockerfile",
    )
    docker_group.add_argument(
        "--distro-registry",
        default=None,
        type=DockerRegistry,
        help="Container registry for the base image (default: None)",
    )
    docker_group.add_argument("--suffix", default=None, help="Image suffix")

    exists_parser = subparsers.add_parser(
        "exists",
        help="check if a container image exists",
    )
    add_project_options(exists_parser)

    exists_group = exists_parser.add_argument_group("exists options")
    exists_group.add_argument(
        "--distro", required=True, help="The base image's distro name (e.g. 'fedora')"
    )
    exists_group.add_argument(
        "--distro-version",
        required=True,
        help="The base image's distro version (e.g. '44')",
    )
    exists_group.add_argument("--suffix", default=None, help="Image suffix")

    args = parser.parse_args()

    if args.verbose >= 2 or os.environ.get("CI") is not None:
        log_level = logging.DEBUG
    elif args.verbose >= 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)-8s| %(message)s", stream=sys.stdout
    )
    logger.setLevel(log_level)

    # Fill in sensible defaults when running outside CI (local debugging).
    if os.environ.get("CI") is None:
        if not args.user:
            import getpass

            args.user = getpass.getuser()

        if not args.user_repo:
            pwd = Path.cwd().name
            args.user_repo = f"{args.user}/{pwd}"

        if not args.upstream_repo:
            pwd = Path.cwd()
            for remote in ["origin", "github", args.user]:
                cmd = Cmd.run(["git", "remote", "get-url", remote])
                if cmd.success:
                    # This splits either https://... or at git@github.com:...
                    url = cmd.stdout[0].rstrip(".git").split(":")[-1]
                    url = url.removeprefix("//")
                    project = Path(url)
                    args.upstream_repo = f"{project.parent}/{project.name}"
                    logger.debug(
                        f"Defaulting to GitHub upstream repo: {args.upstream_repo}"
                    )
                    break

    if not args.upstream_repo:
        logger.error("Failed to get upstream repo, please use --upstream-repo")
        sys.exit(1)

    if args.command == "from-scratch":
        sys.exit(cmd_from_scratch(args))
    elif args.command == "from-base":
        sys.exit(cmd_from_base(args))
    elif args.command == "from-docker":
        sys.exit(cmd_from_docker(args))
    elif args.command == "exists":
        sys.exit(cmd_exists(args))


if __name__ == "__main__":
    main()
