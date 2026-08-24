# SPDX-License-Identifier: MIT
"""Shared fixtures for container-prep tests."""

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
# container-prep.py has a hyphen so we use importlib
_spec = importlib.util.spec_from_file_location(
    "container_prep",
    Path(__file__).parent.parent / "container-prep.py",
)
assert _spec is not None
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
assert _mod is not None
sys.modules["container_prep"] = _mod
_spec.loader.exec_module(_mod)


def _make_cmd(returncode=0, stdout=None, stderr=None, args=None):
    """Helper to create a Cmd instance without running a subprocess."""
    from container_prep import Cmd

    return Cmd(
        args=args or ["mock"],
        returncode=returncode,
        stdout=stdout or [],
        stderr=stderr or [],
    )


@pytest.fixture
def cmd_success():
    """A successful Cmd result."""
    return _make_cmd(returncode=0, stdout=["ok"])


@pytest.fixture
def cmd_failure():
    """A failed Cmd result."""
    return _make_cmd(returncode=1, stderr=["error"])


@pytest.fixture
def mock_cmd_run():
    """Patch Cmd.run to return a successful result by default."""
    with patch("container_prep.Cmd.run") as mock_run:
        mock_run.return_value = _make_cmd(returncode=0)
        yield mock_run


@pytest.fixture
def mock_which():
    """Patch shutil.which to return a fake tool path."""
    with patch("container_prep.shutil.which", return_value="/usr/bin/mock-tool"):
        yield


@pytest.fixture
def mock_skopeo(mock_which, mock_cmd_run):
    """A Skopeo instance with mocked tool lookup and Cmd.run."""
    from container_prep import Skopeo

    return Skopeo()


@pytest.fixture
def mock_buildah(mock_which, mock_cmd_run):
    """A Buildah instance with mocked tool lookup and Cmd.run."""
    from container_prep import Buildah

    return Buildah()


@pytest.fixture
def mock_registry(mock_which, mock_cmd_run):
    """A Registry with mocked Skopeo and Buildah."""
    from container_prep import Credentials, Registry

    creds = Credentials(user="testuser", token="testtoken")
    return Registry(name="example.com", creds=creds)


@pytest.fixture
def github_output_file(tmp_path, monkeypatch):
    """Set GITHUB_OUTPUT to a temp file, return the Path."""
    output_file = tmp_path / "github_output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    return output_file


@pytest.fixture
def no_github_output(monkeypatch):
    """Ensure GITHUB_OUTPUT is not set."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


@pytest.fixture
def tmp_dockerfile(tmp_path):
    """Factory fixture to create a temp Dockerfile with given content."""

    def _create(content: str) -> Path:
        p = tmp_path / "Dockerfile"
        p.write_text(content)
        return p

    return _create


@pytest.fixture
def ci_env(monkeypatch):
    """Set CI=true environment."""
    monkeypatch.setenv("CI", "true")


@pytest.fixture
def no_ci_env(monkeypatch):
    """Ensure CI is not set."""
    monkeypatch.delenv("CI", raising=False)


def make_args(**kwargs) -> Namespace:
    """Create an argparse.Namespace with sensible defaults for testing."""
    defaults = {
        "command": "from-scratch",
        "verbose": 0,
        "dry_run": False,
        "force": False,
        "registry": "ghcr.io",
        "token": "testtoken",
        "upstream_repo": "owner/repo",
        "user": "testuser",
        "user_repo": None,
        "tag": "test-tag",
        "suffix": None,
        "platform": None,
        "distro": "fedora",
        "distro_version": "44",
        "distro_registry": None,
        "packages": "gcc",
        "exec_cmd": None,
        "workdir": "/github/workspace",
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def make_docker_args(**kwargs) -> Namespace:
    """Create an argparse.Namespace for from-docker subcommand."""
    defaults = {
        "command": "from-docker",
        "verbose": 0,
        "dry_run": False,
        "force": False,
        "registry": "ghcr.io",
        "token": "testtoken",
        "upstream_repo": "owner/repo",
        "user": "testuser",
        "user_repo": None,
        "tag": "test-tag",
        "suffix": None,
        "platform": None,
        "distro": None,
        "distro_version": None,
        "distro_registry": None,
        "dockerfile": "/tmp/Dockerfile",
    }
    defaults.update(kwargs)
    return Namespace(**defaults)
