# SPDX-License-Identifier: MIT
"""Tests for the command-line interface (argument parsing and main() dispatch).

These tests invoke the argparser and main() function directly,
with all subprocess calls mocked out.
"""

from unittest.mock import patch

import pytest
from container_prep import Cmd, main


def _cmd(rc=0, stdout=None, stderr=None):
    return Cmd(args=["mock"], returncode=rc, stdout=stdout or [], stderr=stderr or [])


# Common args shared across subcommands
_PROJECT_ARGS = [
    "--upstream-repo",
    "owner/repo",
    "--user",
    "testuser",
    "--token",
    "testtoken",
    "--tag",
    "test-tag",
    "--registry",
    "containers-storage",
]


def _run_main(argv, monkeypatch, github_output_file=None, cmd_side_effect=None):
    """Run main() with the given argv, all tools mocked.

    Returns the SystemExit code.
    """
    monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
    monkeypatch.delenv("CI", raising=False)
    # Avoid local-defaults code trying to auto-detect user/repo
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    side_effect = cmd_side_effect or [_cmd(rc=0)]

    with (
        patch("container_prep.shutil.which", return_value="/usr/bin/mock"),
        patch("container_prep.Cmd.run", side_effect=side_effect) as mock_run,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        return exc_info.value.code, mock_run


class TestTagValidation:
    """Test that --tag is validated by argparse."""

    @pytest.mark.parametrize(
        "tag",
        [
            "valid-tag",
            "2025-08-23.0",
            "v1.0",
            "abc123",
            "a",
            "A_tag.with-all_chars",
        ],
    )
    def test_valid_tags_accepted(self, tag, monkeypatch):
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--packages",
            "gcc",
            "--tag",
            tag,
        ] + _PROJECT_ARGS
        # Should not raise SystemExit(2) for argument errors
        # It will raise SystemExit(0 or 1) from the actual command
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=0),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc in (0, 1)  # not 2 (argparse error)

    @pytest.mark.parametrize(
        "tag",
        [
            ".starts-with-dot",
            "-starts-with-dash",
            "",
        ],
    )
    def test_invalid_tags_rejected(self, tag, monkeypatch):
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--packages",
            "gcc",
            "--tag",
            tag,
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2  # argparse error


class TestFromScratchCLI:
    """Test from-scratch argument parsing and dispatch."""

    def test_minimal_args(self, monkeypatch, github_output_file):
        """Minimal valid from-scratch invocation."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect (not found)
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_missing_distro_fails(self, monkeypatch):
        """--distro is required for from-scratch."""
        argv = [
            "from-scratch",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_missing_distro_version_fails(self, monkeypatch):
        """--distro-version is required for from-scratch."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_missing_tag_fails(self, monkeypatch):
        """--tag is required."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--upstream-repo",
            "owner/repo",
            "--user",
            "testuser",
            "--token",
            "testtoken",
            "--registry",
            "containers-storage",
        ]
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    @pytest.mark.parametrize(
        "distro,version",
        [
            ("fedora", "44"),
            ("alpine", "3.20"),
            ("debian", "bookworm"),
            ("ubuntu", "24.04"),
            ("rocky", "9.3"),
        ],
    )
    def test_various_distros(self, distro, version, monkeypatch, github_output_file):
        """from-scratch works with various distros."""
        argv = [
            "from-scratch",
            "--distro",
            distro,
            "--distro-version",
            version,
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect (not found)
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_with_platform(self, monkeypatch, github_output_file):
        """--platform is accepted."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--platform",
            "linux/arm64",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_with_suffix(self, monkeypatch, github_output_file):
        """--suffix is accepted."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--suffix",
            "custom/path",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_with_workdir(self, monkeypatch, github_output_file):
        """--workdir is accepted."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--workdir",
            "/opt/build",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_dry_run(self, monkeypatch, github_output_file):
        """--dry-run skips push."""
        argv = [
            "from-scratch",
            "--dry-run",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        rc, mock_run = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                # no push expected
            ],
        )
        assert rc == 0
        # Verify push was not called (only login + inspect + bud = 3 calls)
        assert not any("push" in str(c) for c in mock_run.call_args_list)

    def test_force_rebuild(self, monkeypatch, github_output_file):
        """--force rebuilds even if image exists."""
        argv = [
            "from-scratch",
            "--force",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=0),  # inspect (image exists)
                _cmd(rc=0),  # bud (force rebuild)
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_with_distro_registry(self, monkeypatch, github_output_file):
        """--distro-registry is accepted."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--distro-registry",
            "registry.fedoraproject.org",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0


class TestFromBaseCLI:
    """Test from-base argument parsing and dispatch."""

    def test_minimal_args(self, monkeypatch, github_output_file):
        """Minimal valid from-base invocation."""
        argv = [
            "from-base",
            "--base-image",
            "quay.io/myorg/myimage:latest",
            "--suffix",
            "myimage/latest",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect (not found)
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_missing_base_image_fails(self, monkeypatch):
        """--base-image is required for from-base."""
        argv = [
            "from-base",
            "--suffix",
            "myimage/latest",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_missing_suffix_fails(self, monkeypatch):
        """--suffix is required for from-base."""
        argv = [
            "from-base",
            "--base-image",
            "quay.io/myorg/myimage:latest",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_with_packages_and_distro(self, monkeypatch, github_output_file):
        """from-base with --packages requires --distro/--distro-version for PM detection."""
        argv = [
            "from-base",
            "--base-image",
            "quay.io/myorg/myimage:latest",
            "--suffix",
            "myimage/latest",
            "--packages",
            "gcc make",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0


class TestFromDockerCLI:
    """Test from-docker argument parsing and dispatch."""

    def test_minimal_args(self, monkeypatch, github_output_file, tmp_path):
        """Minimal valid from-docker invocation."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM fedora:44\nRUN echo hi\n")
        argv = [
            "from-docker",
            "--dockerfile",
            str(df),
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_missing_dockerfile_arg_fails(self, monkeypatch):
        """--dockerfile is required for from-docker."""
        argv = [
            "from-docker",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_nonexistent_dockerfile_fails(self, monkeypatch, github_output_file):
        """Pointing to a nonexistent Dockerfile returns error."""
        argv = [
            "from-docker",
            "--dockerfile",
            "/nonexistent/Dockerfile",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(argv, monkeypatch, cmd_side_effect=[_cmd(rc=0)])
        assert rc == 1

    def test_with_distro_override(self, monkeypatch, github_output_file, tmp_path):
        """--distro overrides the FROM line's distro."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM fedora:44\nRUN echo hi\n")
        argv = [
            "from-docker",
            "--dockerfile",
            str(df),
            "--distro",
            "myimage",
            "--distro-version",
            "1.0",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_with_suffix(self, monkeypatch, github_output_file, tmp_path):
        """--suffix is accepted for from-docker."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM alpine:3.20\n")
        argv = [
            "from-docker",
            "--dockerfile",
            str(df),
            "--suffix",
            "custom/path",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_dry_run(self, monkeypatch, github_output_file, tmp_path):
        """--dry-run skips push for from-docker."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM fedora:44\n")
        argv = [
            "from-docker",
            "--dry-run",
            "--dockerfile",
            str(df),
        ] + _PROJECT_ARGS
        rc, mock_run = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
            ],
        )
        assert rc == 0
        assert not any("push" in str(c) for c in mock_run.call_args_list)


class TestExistsCLI:
    """Test exists argument parsing and dispatch."""

    def test_image_exists(self, monkeypatch, github_output_file):
        """exists returns 0 when image is found."""
        argv = [
            "exists",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--user-repo",
            "owner/repo",  # same as upstream, no fork
        ] + _PROJECT_ARGS
        # containers-storage skips login, so first Cmd.run is inspect
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # inspect (found)
            ],
        )
        assert rc == 0

    def test_image_not_exists(self, monkeypatch, github_output_file):
        """exists returns 1 when image is not found."""
        argv = [
            "exists",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--user-repo",
            "owner/repo",  # same as upstream, no fork
        ] + _PROJECT_ARGS
        # containers-storage skips login, so first Cmd.run is inspect
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=1),  # inspect (not found)
            ],
        )
        assert rc == 1

    def test_missing_distro_fails(self, monkeypatch):
        """--distro is required for exists."""
        argv = [
            "exists",
            "--distro-version",
            "44",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_missing_distro_version_fails(self, monkeypatch):
        """--distro-version is required for exists."""
        argv = [
            "exists",
            "--distro",
            "fedora",
        ] + _PROJECT_ARGS
        monkeypatch.setattr("sys.argv", ["container-prep.py"] + argv)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


class TestGlobalOptions:
    """Test global options that apply to all subcommands."""

    def test_no_subcommand_fails(self, monkeypatch):
        """Running without a subcommand fails."""
        monkeypatch.setattr("sys.argv", ["container-prep.py"])
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    @pytest.mark.parametrize(
        "verbosity,flag",
        [
            (1, ["-v"]),
            (2, ["-vv"]),
            (0, []),
        ],
    )
    def test_verbose_flag_accepted(
        self, verbosity, flag, monkeypatch, github_output_file
    ):
        """Verbose flags are accepted before subcommand."""
        argv = (
            flag
            + [
                "from-scratch",
                "--distro",
                "fedora",
                "--distro-version",
                "44",
                "--packages",
                "gcc",
            ]
            + _PROJECT_ARGS
        )
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0

    def test_missing_upstream_repo_uses_env(self, monkeypatch, github_output_file):
        """--upstream-repo defaults to $GITHUB_REPOSITORY."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "env-owner/env-repo")
        argv = [
            "exists",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--tag",
            "test-tag",
            "--user",
            "testuser",
            "--token",
            "testtoken",
            "--registry",
            "containers-storage",
        ]
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=0),  # inspect
            ],
        )
        assert rc == 0


class TestBuildOptionsPosition:
    """Test that --dry-run and --force work after the subcommand."""

    @pytest.mark.parametrize(
        "subcommand,extra_args",
        [
            (
                "from-scratch",
                ["--distro", "fedora", "--distro-version", "44", "--packages", "gcc"],
            ),
            ("from-docker", ["--dockerfile", "__PLACEHOLDER__"]),
        ],
    )
    def test_dry_run_after_subcommand(
        self, subcommand, extra_args, monkeypatch, github_output_file, tmp_path
    ):
        """--dry-run works after the subcommand name."""
        # Replace placeholder with real dockerfile if needed
        if "__PLACEHOLDER__" in extra_args:
            df = tmp_path / "Dockerfile"
            df.write_text("FROM fedora:44\n")
            extra_args = [str(df) if a == "__PLACEHOLDER__" else a for a in extra_args]

        argv = [subcommand, "--dry-run"] + extra_args + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect
                _cmd(rc=0),  # bud
            ],
        )
        assert rc == 0

    @pytest.mark.parametrize(
        "subcommand,extra_args",
        [
            (
                "from-scratch",
                ["--distro", "fedora", "--distro-version", "44", "--packages", "gcc"],
            ),
            ("from-docker", ["--dockerfile", "__PLACEHOLDER__"]),
        ],
    )
    def test_force_after_subcommand(
        self, subcommand, extra_args, monkeypatch, github_output_file, tmp_path
    ):
        """--force works after the subcommand name."""
        if "__PLACEHOLDER__" in extra_args:
            df = tmp_path / "Dockerfile"
            df.write_text("FROM fedora:44\n")
            extra_args = [str(df) if a == "__PLACEHOLDER__" else a for a in extra_args]

        argv = [subcommand, "--force"] + extra_args + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=0),  # inspect (exists, but --force)
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0


class TestForkPR:
    """Test --user-repo for fork PR scenarios."""

    def test_user_repo_accepted(self, monkeypatch, github_output_file):
        """--user-repo is accepted and triggers fork PR detection."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--packages",
            "gcc",
            "--user-repo",
            "fork-user/fork-repo",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect upstream (not found)
                _cmd(rc=0),  # inspect fork (found)
            ],
        )
        assert rc == 0

    def test_user_repo_same_as_upstream_not_fork(self, monkeypatch, github_output_file):
        """--user-repo same as --upstream-repo is not a fork PR."""
        argv = [
            "from-scratch",
            "--distro",
            "fedora",
            "--distro-version",
            "44",
            "--packages",
            "gcc",
            "--user-repo",
            "owner/repo",
        ] + _PROJECT_ARGS
        rc, _ = _run_main(
            argv,
            monkeypatch,
            cmd_side_effect=[
                _cmd(rc=0),  # login
                _cmd(rc=1),  # inspect (not found, no fork check)
                _cmd(rc=0),  # bud
                _cmd(rc=0),  # push
            ],
        )
        assert rc == 0
