# SPDX-License-Identifier: MIT
"""Tests for the Registry class in container-prep.py."""

from unittest.mock import patch

import pytest
from container_prep import (
    Cmd,
    Credentials,
    Distro,
    GithubRepo,
    ImageId,
    ImagePath,
    ImageTag,
    Registry,
)


def _make_cmd(returncode=0, stdout=None, stderr=None, args=None):
    """Helper to create a Cmd instance without running a subprocess."""
    return Cmd(
        args=args or ["mock"],
        returncode=returncode,
        stdout=stdout or [],
        stderr=stderr or [],
    )


@pytest.fixture
def creds():
    return Credentials(user="testuser", token="testtoken")


@pytest.fixture
def image_path():
    repo = GithubRepo("owner/repo")
    distro = Distro(name="fedora", version="44")
    tag = ImageTag("latest")
    image_id = ImageId.create(distro=distro, tag=tag)
    return ImagePath.create(repo=repo, image_id=image_id)


class TestRegistryUrl:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_ghcr_io(self, _mock_run, _mock_which):
        reg = Registry(name="ghcr.io", creds=None)
        assert reg.url == "docker://ghcr.io/"

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_containers_storage(self, _mock_run, _mock_which):
        reg = Registry(name="containers-storage", creds=None)
        assert reg.url == "containers-storage:"

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_docker_prefix_already_present(self, _mock_run, _mock_which):
        reg = Registry(name="docker://registry.example.com", creds=None)
        assert reg.url == "docker://registry.example.com/"


class TestRegistryIsLocal:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_containers_storage_is_local(self, _mock_run, _mock_which):
        reg = Registry(name="containers-storage", creds=None)
        assert reg.is_local is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_ghcr_io_is_not_local(self, _mock_run, _mock_which):
        reg = Registry(name="ghcr.io", creds=None)
        assert reg.is_local is False


class TestRegistryLogin:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_no_creds_returns_true(self, _mock_run, _mock_which):
        reg = Registry(name="ghcr.io", creds=None)
        assert reg.login() is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_local_registry_returns_true(self, _mock_run, _mock_which, creds):
        reg = Registry(name="containers-storage", creds=creds)
        assert reg.login() is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_login_success(self, mock_run, _mock_which, creds):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="ghcr.io", creds=creds)
        assert reg.login() is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_login_failure(self, mock_run, _mock_which, creds):
        mock_run.return_value = _make_cmd(returncode=1)
        reg = Registry(name="ghcr.io", creds=creds)
        assert reg.login() is False


class TestRegistryCheckImage:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_exists(self, mock_run, _mock_which, creds, image_path):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="ghcr.io", creds=creds)
        result = reg.check_image(image_path)
        assert result.exists is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_not_exists(self, mock_run, _mock_which, creds, image_path):
        # First call is login (rc=0), second call is inspect (rc=1)
        mock_run.side_effect = [
            _make_cmd(returncode=0),
            _make_cmd(returncode=1),
        ]
        reg = Registry(name="ghcr.io", creds=creds)
        result = reg.check_image(image_path)
        assert result.exists is False

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_full_path_includes_registry_url(
        self, mock_run, _mock_which, creds, image_path
    ):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="ghcr.io", creds=creds)
        result = reg.check_image(image_path)
        expected = f"docker://ghcr.io/{image_path}"
        assert result.url == expected

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_creds_passed_to_inspect(self, mock_run, _mock_which, creds, image_path):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="ghcr.io", creds=creds)
        reg.check_image(image_path)

        # Find the inspect call (may be preceded by a login call)
        inspect_calls = [
            c
            for c in mock_run.call_args_list
            if any("inspect" in str(a) for a in c[0][0])
            if isinstance(c[0][0], list)
        ]
        assert len(inspect_calls) >= 1
        inspect_args = inspect_calls[-1][0][0]
        creds_arg = f"--creds={creds.user}:{creds.token}"
        assert creds_arg in inspect_args


class TestRegistryPush:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_remote_push(self, mock_run, _mock_which, creds, image_path):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="ghcr.io", creds=creds)
        registry_image = reg.check_image(image_path)

        mock_run.reset_mock()
        mock_run.return_value = _make_cmd(returncode=0)
        reg.push(registry_image)

        # buildah push should be called with the registry-qualified local name
        push_call = mock_run.call_args
        push_args = push_call[0][0]
        assert "push" in push_args
        assert registry_image.local_name in push_args
        assert "ghcr.io/" in registry_image.local_name

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_local_push(self, mock_run, _mock_which, creds, image_path):
        mock_run.return_value = _make_cmd(returncode=0)
        reg = Registry(name="containers-storage", creds=creds)
        registry_image = reg.check_image(image_path)

        mock_run.reset_mock()
        mock_run.return_value = _make_cmd(returncode=0)
        reg.push(registry_image)

        # For local, buildah push is called with localhost/ prefix
        push_call = mock_run.call_args
        push_args = push_call[0][0]
        assert "push" in push_args
        assert registry_image.local_name in push_args
        assert registry_image.local_name.startswith("localhost/")
