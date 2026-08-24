# SPDX-License-Identifier: MIT
"""Tests for GithubOutput and GithubLog classes."""

import pytest
from container_prep import GithubLog, GithubOutput


class TestGithubOutput:
    def test_add_returns_self(self):
        output = GithubOutput()
        result = output.add("key", "value")
        assert result is output

    def test_add_chaining(self):
        output = GithubOutput()
        result = output.add("a", "1").add("b", "2")
        assert result is output

    def test_str_single(self):
        output = GithubOutput()
        output.add("image", "ghcr.io/foo/bar:latest")
        assert str(output) == "image=ghcr.io/foo/bar:latest"

    def test_str_multiple(self):
        output = GithubOutput()
        output.add("image", "ghcr.io/foo/bar:latest")
        output.add("build-skipped", "false")
        assert str(output) == ("image=ghcr.io/foo/bar:latest\nbuild-skipped=false")

    def test_str_empty(self):
        output = GithubOutput()
        assert str(output) == ""

    def test_write_appends_to_file(self, github_output_file):
        output = GithubOutput()
        output.add("image", "ghcr.io/foo/bar:latest")
        output.add("build-skipped", "true")
        output.write()

        content = github_output_file.read_text()
        assert "image=ghcr.io/foo/bar:latest\n" in content
        assert "build-skipped=true\n" in content

    def test_write_appends_not_overwrites(self, github_output_file):
        github_output_file.write_text("existing=data\n")

        output = GithubOutput()
        output.add("new", "value")
        output.write()

        content = github_output_file.read_text()
        assert content.startswith("existing=data\n")
        assert "new=value\n" in content

    def test_write_without_github_output(self, no_github_output):
        """write() must not crash when GITHUB_OUTPUT is unset."""
        output = GithubOutput()
        output.add("key", "value")
        output.write()  # should not raise


class TestGithubLog:
    def test_group_ci(self, ci_env, capsys):
        log = GithubLog()
        with log.group("my title"):
            print("inside group")

        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert lines[0] == "::group::my title"
        assert lines[1] == "inside group"
        assert lines[2] == "::endgroup::"

    def test_warning_ci(self, ci_env, capsys):
        log = GithubLog()
        log.warning("something went wrong")

        captured = capsys.readouterr()
        assert captured.out.strip() == "::warning::something went wrong"

    def test_error_ci(self, ci_env, capsys):
        log = GithubLog()
        log.error("build failed")

        captured = capsys.readouterr()
        assert captured.out.strip() == "::error::build failed"

    def test_group_endgroup_on_exception(self, ci_env, capsys):
        """::endgroup:: must be emitted even when the body raises."""
        log = GithubLog()
        with pytest.raises(RuntimeError), log.group("failing"):
            raise RuntimeError("boom")

        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert lines[0] == "::group::failing"
        assert lines[1] == "::endgroup::"

    def test_group_local_no_stdout(self, no_ci_env, capsys):
        """In local mode, group() should not print ::group:: to stdout."""
        log = GithubLog()
        with log.group("local title"):
            pass

        captured = capsys.readouterr()
        assert "::group::" not in captured.out
