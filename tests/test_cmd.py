# SPDX-License-Identifier: MIT
"""Tests for the Cmd dataclass in container-prep.py."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from container_prep import Cmd


class TestCmd:
    def test_succes(self):
        cmd = Cmd(args=["true"], returncode=0, stdout=[], stderr=[])
        assert cmd.success is True
        cmd = Cmd(args=["false"], returncode=1, stdout=[], stderr=[])
        assert cmd.success is False
        cmd = Cmd(args=["killed"], returncode=-9, stdout=[], stderr=[])
        assert cmd.success is False

    def test_constructor(self):
        cmd = Cmd(args=["x", "y"], returncode=42, stdout=["out"], stderr=["err"])
        assert cmd.args == ["x", "y"]
        assert cmd.returncode == 42
        assert cmd.stdout == ["out"]
        assert cmd.stderr == ["err"]

        # multiple lines in stdout
        cmd = Cmd(args=["x"], returncode=0, stdout=["a", "b", "c"], stderr=[])
        assert cmd.stdout == ["a", "b", "c"]

        # nothing in stdout
        cmd = Cmd(args=["x"], returncode=0, stdout=[], stderr=[])
        assert cmd.stdout == []
        assert cmd.stderr == []


class TestCmdRun:
    """Tests for Cmd.run() with stream=False (the default)."""

    @pytest.mark.parametrize("which", ["stdout", "stderr"])
    @patch("container_prep.subprocess.run")
    def test_stdout_split_into_lines(self, mock_run, which):
        data = "line1\nline2\nline3"

        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "hello"],
            returncode=0,
            stdout=data if which == "stdout" else "",
            stderr=data if which == "stderr" else "",
        )
        result = Cmd.run(["echo", "hello"])
        stdout_expected = data.splitlines() if which == "stdout" else []
        stderr_expected = data.splitlines() if which == "stderr" else []
        assert result.stdout == stdout_expected
        assert result.stderr == stderr_expected

    @patch("container_prep.subprocess.run")
    def test_returncode_propagated(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["cmd"],
            returncode=42,
            stdout="",
            stderr="",
        )
        result = Cmd.run(["cmd"])
        assert result.returncode == 42

    @patch("container_prep.subprocess.run")
    def test_args_passed_to_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )
        Cmd.run(["git", "status"])
        mock_run.assert_called_once_with(
            ["git", "status"],
            input=None,
            text=True,
            capture_output=True,
            check=False,
        )

    @patch("container_prep.subprocess.run")
    def test_input_passed_through(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["cat"],
            returncode=0,
            stdout="hello",
            stderr="",
        )
        Cmd.run(["cat"], input="hello")
        mock_run.assert_called_once_with(
            ["cat"],
            input="hello",
            text=True,
            capture_output=True,
            check=False,
        )

    @patch("container_prep.subprocess.run")
    def test_result_is_cmd_instance(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["cmd"],
            returncode=0,
            stdout="output",
            stderr="",
        )
        result = Cmd.run(["cmd"])
        assert isinstance(result, Cmd)

    @patch("container_prep.subprocess.run")
    def test_args_stored_on_result(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ls", "-la"],
            returncode=0,
            stdout="",
            stderr="",
        )
        result = Cmd.run(["ls", "-la"])
        assert result.args == ["ls", "-la"]


class TestCmdRunStreaming:
    """Tests for Cmd.run() with stream=True."""

    @patch("container_prep.subprocess.Popen")
    def test_stdout_lines_captured(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["line1\n", "line2\n", "line3\n"])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        result = Cmd.run(["cmd"], stream=True)
        assert result.stdout == ["line1", "line2", "line3"]

    @patch("container_prep.subprocess.Popen")
    def test_stderr_is_empty_list(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["output\n"])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        result = Cmd.run(["cmd"], stream=True)
        assert result.stderr == []

    @patch("container_prep.subprocess.Popen")
    def test_returncode_from_wait(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 7
        mock_popen.return_value = proc

        result = Cmd.run(["cmd"], stream=True)
        assert result.returncode == 7
        proc.wait.assert_called_once()

    @patch("container_prep.subprocess.Popen")
    def test_popen_called_with_correct_args(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        Cmd.run(["git", "log"], stream=True)
        mock_popen.assert_called_once_with(
            ["git", "log"],
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    @patch("container_prep.subprocess.Popen")
    def test_input_writes_to_stdin_and_closes(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["ok\n"])
        proc.stdin = MagicMock()
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        Cmd.run(["cmd"], input="data", stream=True)
        proc.stdin.write.assert_called_once_with("data")
        proc.stdin.close.assert_called_once()

    @patch("container_prep.subprocess.Popen")
    def test_popen_uses_pipe_stdin_when_input_given(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stdin = MagicMock()
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        Cmd.run(["cmd"], input="data", stream=True)
        mock_popen.assert_called_once_with(
            ["cmd"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    @patch("container_prep.subprocess.Popen")
    def test_empty_output_gives_empty_list(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        result = Cmd.run(["cmd"], stream=True)
        assert result.stdout == []

    @patch("container_prep.subprocess.Popen")
    def test_lines_have_newlines_stripped(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter(["with newline\n", "no newline"])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        result = Cmd.run(["cmd"], stream=True)
        assert result.stdout == ["with newline", "no newline"]

    @patch("container_prep.subprocess.Popen")
    def test_args_stored_on_result(self, mock_popen):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.stdin = None
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        result = Cmd.run(["ls", "-la"], stream=True)
        assert result.args == ["ls", "-la"]
