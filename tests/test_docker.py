# SPDX-License-Identifier: MIT
"""Unit tests for DockerCommand, DockerCommand.Mount, and Dockerfile."""

from pathlib import Path

import pytest
from container_prep import (
    Distro,
    DockerCommand,
    Dockerfile,
    DockerInstruction,
    DockerRegistry,
    Env,
    Platform,
)


class TestDockerCommandMount:
    def test_str_explicit_source_and_target(self):
        m = DockerCommand.Mount(source=Path("/src"), target=Path("/tgt"))
        assert str(m) == "--mount=type=bind,source=/src,target=/tgt"

    def test_str_default_target_uses_source(self):
        m = DockerCommand.Mount(source=Path("/data"))
        assert m.target is None
        assert str(m) == "--mount=type=bind,source=/data,target=/data"

    @pytest.mark.parametrize(
        "mount_type",
        ["cache", "tmpfs", "secret", "ssh"],
    )
    def test_str_different_mount_types(self, mount_type):
        m = DockerCommand.Mount(
            source=Path("/src"), target=Path("/tgt"), type=mount_type
        )
        assert str(m) == f"--mount=type={mount_type},source=/src,target=/tgt"


class TestDockerCommand:
    def test_run_simple(self):
        cmd = DockerCommand.run("echo hello")
        assert str(cmd) == "RUN echo hello"

    def test_run(self):
        mounts = [
            DockerCommand.Mount(source=Path("/a"), target=Path("/b")),
            DockerCommand.Mount(source=Path("/c"), target=Path("/d")),
        ]
        cmd = DockerCommand.run("make install", mounts=mounts)
        result = str(cmd)
        # Mounts are joined with ' \\\n    ' and then ' \\\n    cmd'
        assert result.startswith("RUN --mount=type=bind,source=/a,target=/b")
        assert "--mount=type=bind,source=/c,target=/d" in result
        assert result.endswith("make install")
        # Line continuations present
        assert "\\\n" in result

        env = [Env("CC", "gcc"), Env("CFLAGS", "-O2")]
        cmd = DockerCommand.run("make", env=env)
        assert str(cmd) == "RUN CC=gcc CFLAGS=-O2 make"

        mounts = [DockerCommand.Mount(source=Path("/src"), target=Path("/tgt"))]
        env = [Env("KEY", "VAL")]
        cmd = DockerCommand.run("build", mounts=mounts, env=env)
        result = str(cmd)
        # Mount comes first, then env+cmd on last line
        assert result.startswith("RUN --mount=")
        assert "KEY=VAL build" in result

    def test_from(self):
        d = Distro("fedora", "44")
        cmd = DockerCommand.from_distro(distro=d, registry=None)
        assert str(cmd) == "FROM fedora:44"

        cmd = DockerCommand.from_distro(
            distro=d, registry=DockerRegistry("registry.fedoraproject.org")
        )
        assert str(cmd) == "FROM registry.fedoraproject.org/fedora:44"

        cmd = DockerCommand.from_distro(distro=d, registry=None, platform="linux/arm64")
        assert str(cmd) == "FROM --platform=linux/arm64 fedora:44"

        cmd = DockerCommand.from_distro(
            distro=d,
            registry=DockerRegistry("quay.io"),
            platform="linux/amd64",
        )
        assert str(cmd) == "FROM --platform=linux/amd64 quay.io/fedora:44"

    def test_env(self):
        cmd = DockerCommand.env("KEY", "VALUE")
        assert str(cmd) == "ENV KEY=VALUE"

    def test_workdir(self):
        cmd = DockerCommand.workdir("/app")
        assert str(cmd) == "WORKDIR /app"

    def test_copy(self):
        cmd = DockerCommand.copy(".", "/app")
        assert str(cmd) == "COPY . /app"

    def test_comment(self):
        cmd = DockerCommand.comment("text")
        assert str(cmd) == "# text"


class TestDockerfile:
    def test_str_joins_with_newlines_and_trailing_newline(self):
        df = Dockerfile()
        df.add(DockerCommand.from_distro(Distro("fedora", "44"), registry=None))
        df.add(DockerCommand.run("echo hi"))
        result = str(df)
        assert result == "FROM fedora:44\nRUN echo hi\n"

    def test_parse_simple(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\nRUN echo hi\nENV FOO=bar\nWORKDIR /app\n")
        df = Dockerfile.parse(p)
        assert len(df.cmds) == 4
        assert df.cmds[0].type == DockerInstruction.FROM
        assert df.cmds[1].type == DockerInstruction.RUN
        assert df.cmds[2].type == DockerInstruction.ENV
        assert df.cmds[3].type == DockerInstruction.WORKDIR

    def test_parse_handles_comments(self, tmp_dockerfile):
        p = tmp_dockerfile("# this is a comment\nFROM fedora:44\n")
        df = Dockerfile.parse(p)
        assert df.cmds[0].type == DockerInstruction.COMMENT
        assert df.cmds[0].cmd == "this is a comment"
        assert df.cmds[1].type == DockerInstruction.FROM

    def test_parse_handles_line_continuations(self, tmp_dockerfile):
        content = "FROM fedora:44\nRUN echo hello \\\n    && echo world\n"
        p = tmp_dockerfile(content)
        df = Dockerfile.parse(p)
        run_cmd = df.cmds[1]
        assert run_cmd.type == DockerInstruction.RUN
        assert "echo hello" in run_cmd.cmd
        assert "echo world" in run_cmd.cmd

    def test_parse_skips_empty_lines(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\n\n\nRUN echo hi\n")
        df = Dockerfile.parse(p)
        assert len(df.cmds) == 2

    def test_parse_file_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            Dockerfile.parse(missing)

    def test_distro_extracts_name_and_version(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\n")
        df = Dockerfile.parse(p)
        assert df.distro is not None
        assert df.distro.distro.name == "fedora"
        assert df.distro.distro.version == "44"

    def test_distro_handles_platform(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM --platform=linux/arm64 fedora:44\n")
        df = Dockerfile.parse(p)
        assert df.distro is not None
        assert df.distro.platform == Platform("linux/arm64")
        assert df.distro.distro.name == "fedora"
        assert df.distro.distro.version == "44"

    def test_distro_handles_registry_prefix(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM registry.fedoraproject.org/fedora:44\n")
        df = Dockerfile.parse(p)
        assert df.distro is not None
        assert df.distro.registry_name == DockerRegistry("registry.fedoraproject.org")
        assert df.distro.distro.name == "fedora"
        assert df.distro.distro.version == "44"

    def test_distro_handles_as_builder(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44 AS builder\n")
        df = Dockerfile.parse(p)
        assert df.distro is not None
        assert df.distro.distro.name == "fedora"
        assert df.distro.distro.version == "44"

    def test_distro_returns_none_for_no_from(self):
        df = Dockerfile()
        df.add(DockerCommand.run("echo hi"))
        assert df.distro is None

    def test_distro_returns_none_for_from_without_version(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM scratch\n")
        df = Dockerfile.parse(p)
        assert df.distro is None

    def test_override_distro(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\nRUN echo hi\n")
        df = Dockerfile.parse(p)
        df.override_distro(Distro("ubuntu", "24.04"))
        result = str(df)
        assert "FROM ubuntu:24.04" in result
        assert "fedora" not in result

    def test_override_platform(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\n")
        df = Dockerfile.parse(p)
        df.override_platform(Platform("linux/arm64"))
        result = str(df)
        assert "FROM --platform=linux/arm64 fedora:44" in result

    def test_override_registry(self, tmp_dockerfile):
        p = tmp_dockerfile("FROM fedora:44\n")
        df = Dockerfile.parse(p)
        df.override_registry(DockerRegistry("quay.io"))
        result = str(df)
        assert "FROM quay.io/fedora:44" in result

    def test_override_platform_without_distro_raises(self):
        df = Dockerfile()
        df.add(DockerCommand.run("echo hi"))
        with pytest.raises(ValueError):
            df.override_platform(Platform("linux/arm64"))
