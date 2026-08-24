# SPDX-License-Identifier: MIT
"""Integration tests for container-prep.py high-level functions."""

from unittest.mock import patch

from container_prep import (
    Cmd,
    Credentials,
    Distro,
    DockerCommand,
    Dockerfile,
    GithubRepo,
    ImageId,
    ImagePath,
    ImageTag,
    Registry,
    RegistryImage,
    build_and_push,
    cmd_exists,
    cmd_from_docker,
    cmd_from_scratch,
    images_exist,
)

from tests.conftest import make_args, make_docker_args


def _cmd(rc=0, stdout=None, stderr=None):
    """Create a Cmd result for mocking."""
    return Cmd(args=["mock"], returncode=rc, stdout=stdout or [], stderr=stderr or [])


def _make_registry(creds=True):
    """Create a Registry with optional credentials."""
    c = Credentials(user="testuser", token="testtoken") if creds else None
    return Registry(name="ghcr.io", creds=c)


def _make_image_id(distro="fedora", version="44", tag="test-tag"):
    return ImageId.create(
        distro=Distro(distro, version),
        tag=ImageTag(tag),
    )


def _make_image_path(
    repo_str="owner/repo", distro="fedora", version="44", tag="test-tag"
):
    repo = GithubRepo(repo_str)
    image_id = _make_image_id(distro, version, tag)
    return ImagePath.create(repo=repo, image_id=image_id)


def _make_registry_image(registry, image_path, exists=False):
    full_path = f"{registry.url}{image_path}"
    return RegistryImage(
        exists=exists,
        image_path=image_path,
        _full_path=full_path,
        _registry=registry,
    )


def _make_dockerfile():
    df = Dockerfile()
    df.add(DockerCommand.from_distro(Distro("fedora", "44"), registry=None))
    return df


class TestImagesExist:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_upstream_image_exists(self, mock_cmd_run, _mock_which):
        """When the upstream image exists, return it with exists=True."""
        # login succeeds, inspect succeeds
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=0),  # skopeo inspect (upstream) -> exists
        ]
        registry = _make_registry()
        upstream = GithubRepo("owner/repo")
        image_id = _make_image_id()

        result = images_exist(registry, upstream, None, image_id)
        assert result.exists is True

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_upstream_not_exists_no_fork(self, mock_cmd_run, _mock_which):
        """When upstream doesn't exist and no fork, return exists=False."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect (upstream) -> not found
        ]
        registry = _make_registry()
        upstream = GithubRepo("owner/repo")
        image_id = _make_image_id()

        result = images_exist(registry, upstream, None, image_id)
        assert result.exists is False

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_fork_pr_user_image_exists(self, mock_cmd_run, _mock_which):
        """Fork PR: upstream missing, user image exists → return user image."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect (upstream) -> not found
            _cmd(rc=0),  # skopeo inspect (user) -> exists
        ]
        registry = _make_registry()
        upstream = GithubRepo("owner/repo")
        user = GithubRepo("forker/repo")
        image_id = _make_image_id()

        result = images_exist(registry, upstream, user, image_id)
        assert result.exists is True
        # The returned image should be from the user repo path
        assert "forker/repo" in result.image_path.path

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_fork_pr_neither_exists(self, mock_cmd_run, _mock_which):
        """Fork PR: neither upstream nor user image exists → return upstream with exists=False."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect (upstream) -> not found
            _cmd(rc=1),  # skopeo inspect (user) -> not found
        ]
        registry = _make_registry()
        upstream = GithubRepo("owner/repo")
        user = GithubRepo("forker/repo")
        image_id = _make_image_id()

        result = images_exist(registry, upstream, user, image_id)
        assert result.exists is False
        # Should return the upstream image reference
        assert "owner/repo" in result.image_path.path


class TestBuildAndPush:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_login_fails(self, mock_cmd_run, _mock_which):
        """Login failure → returns False."""
        mock_cmd_run.return_value = _cmd(rc=1)  # login fails
        registry = _make_registry()
        image_path = _make_image_path()
        image = _make_registry_image(registry, image_path, exists=False)
        dockerfile = _make_dockerfile()

        result = build_and_push(registry, dockerfile, image)
        assert result is False

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_build_fails(self, mock_cmd_run, _mock_which):
        """Build failure → returns False."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # login succeeds
            _cmd(rc=1),  # buildah bud fails
        ]
        registry = _make_registry()
        image_path = _make_image_path()
        image = _make_registry_image(registry, image_path, exists=False)
        dockerfile = _make_dockerfile()

        result = build_and_push(registry, dockerfile, image)
        assert result is False

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_push_fails(self, mock_cmd_run, _mock_which):
        """Push failure → returns False."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # login succeeds
            _cmd(rc=0),  # buildah bud succeeds
            _cmd(rc=1),  # buildah push fails
        ]
        registry = _make_registry()
        image_path = _make_image_path()
        image = _make_registry_image(registry, image_path, exists=False)
        dockerfile = _make_dockerfile()

        result = build_and_push(registry, dockerfile, image)
        assert result is False

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_dry_run_skips_push(self, mock_cmd_run, _mock_which, github_output_file):
        """Dry run → skips push, returns True, writes GithubOutput."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # login succeeds
            _cmd(rc=0),  # buildah bud succeeds
            # no push call expected
        ]
        registry = _make_registry()
        image_path = _make_image_path()
        image = _make_registry_image(registry, image_path, exists=False)
        dockerfile = _make_dockerfile()

        result = build_and_push(registry, dockerfile, image, dry_run=True)
        assert result is True

        output = github_output_file.read_text()
        assert "build-skipped=false" in output
        # push should not have been called (only 2 calls: login + bud)
        assert mock_cmd_run.call_count == 2

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_full_success(self, mock_cmd_run, _mock_which, github_output_file):
        """Full success → returns True, writes image url and build-skipped=false."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # login succeeds
            _cmd(rc=0),  # buildah bud succeeds
            _cmd(rc=0),  # buildah push succeeds
        ]
        registry = _make_registry()
        image_path = _make_image_path()
        image = _make_registry_image(registry, image_path, exists=False)
        dockerfile = _make_dockerfile()

        result = build_and_push(registry, dockerfile, image)
        assert result is True

        output = github_output_file.read_text()
        assert "build-skipped=false" in output
        assert "image=" in output
        assert image.local_name in output


class TestCmdFromScratch:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_cached(self, mock_cmd_run, _mock_which, github_output_file):
        """Image exists in cache → returns 0, writes build-skipped=true."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=0),  # skopeo inspect → image exists
        ]
        args = make_args(
            distro="fedora", distro_version="44", packages="gcc", tag="test"
        )

        rc = cmd_from_scratch(args)
        assert rc == 0

        output = github_output_file.read_text()
        assert "build-skipped=true" in output

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_not_cached_builds(
        self, mock_cmd_run, _mock_which, github_output_file
    ):
        """Image not cached, build succeeds → returns 0."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect → not found
            _cmd(rc=0),  # registry login (build_and_push)
            _cmd(rc=0),  # buildah bud
            _cmd(rc=0),  # buildah push
        ]
        args = make_args(
            distro="fedora", distro_version="44", packages="gcc", tag="test"
        )

        rc = cmd_from_scratch(args)
        assert rc == 0

        output = github_output_file.read_text()
        assert "build-skipped=false" in output

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_unknown_distro_warns_but_continues(
        self, mock_cmd_run, _mock_which, github_output_file
    ):
        """Unknown distro → warns but continues (build still runs)."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect → not found
            _cmd(rc=0),  # registry login (build_and_push)
            _cmd(rc=0),  # buildah bud
            _cmd(rc=0),  # buildah push
        ]
        args = make_args(
            distro="unknowndistro", distro_version="1", packages="gcc", tag="test"
        )

        rc = cmd_from_scratch(args)
        assert rc == 0


class TestCmdFromDocker:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_dockerfile_not_found(self, mock_cmd_run, _mock_which):
        """Dockerfile not found → returns 1."""
        args = make_docker_args(dockerfile="/nonexistent/Dockerfile")

        rc = cmd_from_docker(args)
        assert rc == 1

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_parses_from_and_builds(
        self, mock_cmd_run, _mock_which, tmp_path, github_output_file
    ):
        """Parses FROM from Dockerfile, builds successfully → returns 0."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM fedora:44\nRUN dnf install -y gcc\n")

        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect → not found
            _cmd(rc=0),  # registry login (build_and_push)
            _cmd(rc=0),  # buildah bud
            _cmd(rc=0),  # buildah push
        ]
        args = make_docker_args(dockerfile=str(dockerfile))

        rc = cmd_from_docker(args)
        assert rc == 0

        output = github_output_file.read_text()
        assert "build-skipped=false" in output

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_distro_override_from_args(
        self, mock_cmd_run, _mock_which, tmp_path, github_output_file
    ):
        """Distro override from args changes the image identity."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM fedora:44\nRUN echo hello\n")

        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect → not found
            _cmd(rc=0),  # registry login (build_and_push)
            _cmd(rc=0),  # buildah bud
            _cmd(rc=0),  # buildah push
        ]
        args = make_docker_args(
            dockerfile=str(dockerfile),
            distro="ubuntu",
            distro_version="24.04",
        )

        rc = cmd_from_docker(args)
        assert rc == 0

        # The output image path should reflect the overridden distro
        output = github_output_file.read_text()
        assert "ubuntu/24.04" in output


class TestCmdExists:
    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_exists_returns_0(self, mock_cmd_run, _mock_which):
        """Image exists → returns 0."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=0),  # skopeo inspect → exists
        ]
        args = make_args(
            command="exists", distro="fedora", distro_version="44", tag="test"
        )

        rc = cmd_exists(args)
        assert rc == 0

    @patch("container_prep.shutil.which", return_value="/usr/bin/mock")
    @patch("container_prep.Cmd.run")
    def test_image_not_exists_returns_1(self, mock_cmd_run, _mock_which):
        """Image doesn't exist → returns 1."""
        mock_cmd_run.side_effect = [
            _cmd(rc=0),  # skopeo login
            _cmd(rc=1),  # skopeo inspect → not found
        ]
        args = make_args(
            command="exists", distro="fedora", distro_version="44", tag="test"
        )

        rc = cmd_exists(args)
        assert rc == 1
