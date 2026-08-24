# SPDX-License-Identifier: MIT
"""Unit tests for PackageManager."""

import pytest
from container_prep import Distro, DockerInstruction, Env, PackageManager


class TestPackageManagerGet:
    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("alpine", "3.20"),
            ("archlinux", "latest"),
            ("centos", "7"),
            ("centos-stream", "9"),
            ("debian", "bookworm"),
            ("ubuntu", "24.04"),
            ("fedora", "44"),
            ("opensuse-tumbleweed", "latest"),
            ("rocky", "8.9"),
            ("rocky", "9.3"),
        ],
    )
    def test_returns_instance_for_known_distros(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        assert isinstance(pm, PackageManager)
        assert pm.upgrade
        assert pm.install
        assert pm.clean

    def test_unknown_distro_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown distro"):
            PackageManager.get(Distro("gentoo", "2"))

    def test_rocky_8_uses_powertools(self):
        pm = PackageManager.get(Distro("rocky", "8.9"))
        upgrade_str = " ".join(pm.upgrade)
        assert "powertools" in upgrade_str
        assert "crb" not in upgrade_str

    def test_rocky_9_uses_crb(self):
        pm = PackageManager.get(Distro("rocky", "9.3"))
        upgrade_str = " ".join(pm.upgrade)
        assert "crb" in upgrade_str
        assert "powertools" not in upgrade_str


class TestPackageManagerRequiredEnvs:
    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("fedora", "44"),
            ("alpine", "3.20"),
            ("centos", "7"),
            ("rocky", "9.3"),
            ("archlinux", "latest"),
            ("opensuse-tumbleweed", "latest"),
        ],
    )
    def test_empty_for_most_distros(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        assert pm.required_envs() == []

    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("debian", "bookworm"),
            ("ubuntu", "24.04"),
        ],
    )
    def test_debian_frontend_for_debian_and_ubuntu(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        assert pm.required_envs() == ["DEBIAN_FRONTEND"]


class TestPackageManagerDockerCommandsWithPackages:
    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("alpine", "3.20"),
            ("archlinux", "latest"),
            ("centos", "7"),
            ("centos-stream", "9"),
            ("debian", "bookworm"),
            ("ubuntu", "24.04"),
            ("fedora", "44"),
            ("opensuse-tumbleweed", "latest"),
            ("rocky", "8.9"),
            ("rocky", "9.3"),
        ],
    )
    def test_contains_run_with_package_names(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        cmds = pm.docker_commands(["gcc", "make"])
        run_cmds = [c for c in cmds if c.type == DockerInstruction.RUN]
        assert run_cmds, "Expected at least one RUN instruction"
        # The main RUN (last one) must contain the package names
        main_run = run_cmds[-1]
        assert "gcc" in main_run.cmd
        assert "make" in main_run.cmd

    def test_debian_has_setup_and_env_before_main_run(self):
        pm = PackageManager.get(Distro("debian", "bookworm"))
        cmds = pm.docker_commands(["gcc"])
        types = [c.type for c in cmds]
        # Setup RUN comes first, then ENV, then the main RUN
        assert types[0] == DockerInstruction.RUN  # setup
        assert types[1] == DockerInstruction.ENV  # DEBIAN_FRONTEND
        assert types[2] == DockerInstruction.RUN  # main install

    def test_ubuntu_has_setup_and_env_before_main_run(self):
        pm = PackageManager.get(Distro("ubuntu", "24.04"))
        cmds = pm.docker_commands(["gcc"])
        types = [c.type for c in cmds]
        assert types[0] == DockerInstruction.RUN
        assert types[1] == DockerInstruction.ENV
        assert types[2] == DockerInstruction.RUN

    def test_commands_chained_with_and(self):
        """The main RUN should &&-chain upgrade, install, and clean."""
        pm = PackageManager.get(Distro("fedora", "44"))
        cmds = pm.docker_commands(["gcc"])
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        assert "&&" in main_run.cmd


class TestPackageManagerDockerCommandsWithoutPackages:
    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("alpine", "3.20"),
            ("fedora", "44"),
            ("debian", "bookworm"),
            ("rocky", "9.3"),
        ],
    )
    def test_no_install_step(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        cmds = pm.docker_commands()
        # The main RUN should not contain the install command
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        for install_cmd in pm.install:
            assert install_cmd not in main_run.cmd

    @pytest.mark.parametrize(
        "distro_name, distro_version",
        [
            ("alpine", "3.20"),
            ("fedora", "44"),
            ("debian", "bookworm"),
            ("rocky", "9.3"),
        ],
    )
    def test_still_has_upgrade_and_clean(self, distro_name, distro_version):
        pm = PackageManager.get(Distro(distro_name, distro_version))
        cmds = pm.docker_commands()
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        # upgrade commands are present
        for upgrade_cmd in pm.upgrade:
            assert upgrade_cmd in main_run.cmd
        # clean commands are present
        for clean_cmd in pm.clean:
            assert clean_cmd in main_run.cmd

    def test_produces_valid_run_instruction(self):
        pm = PackageManager.get(Distro("fedora", "44"))
        cmds = pm.docker_commands()
        run_cmds = [c for c in cmds if c.type == DockerInstruction.RUN]
        assert len(run_cmds) >= 1
        # Should render to a valid "RUN ..." string
        assert str(run_cmds[-1]).startswith("RUN ")


class TestAlpineSpecificBehavior:
    def test_uses_apk_commands(self):
        pm = PackageManager.get(Distro("alpine", "3.20"))
        cmds = pm.docker_commands(["curl", "git"])
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        assert "apk update" in main_run.cmd
        assert "apk upgrade" in main_run.cmd
        assert "apk add curl git" in main_run.cmd
        assert "rm -rf /var/cache/apk/*" in main_run.cmd


class TestFedoraSpecificBehavior:
    def test_uses_dnf_commands(self):
        pm = PackageManager.get(Distro("fedora", "44"))
        cmds = pm.docker_commands(["gcc", "meson"])
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        assert "dnf upgrade -y --setopt=install_weak_deps=False" in main_run.cmd
        assert (
            "dnf install -y --setopt=install_weak_deps=False gcc meson" in main_run.cmd
        )
        assert "dnf clean all" in main_run.cmd


class TestDebianSpecificBehavior:
    def test_has_apt_config_setup(self):
        pm = PackageManager.get(Distro("debian", "bookworm"))
        cmds = pm.docker_commands(["gcc"])
        setup_run = cmds[0]
        assert setup_run.type == DockerInstruction.RUN
        assert "apt.conf" in setup_run.cmd or "APT" in setup_run.cmd

    def test_has_debian_frontend_env(self):
        pm = PackageManager.get(Distro("debian", "bookworm"))
        cmds = pm.docker_commands(["gcc"])
        env_cmds = [c for c in cmds if c.type == DockerInstruction.ENV]
        assert len(env_cmds) == 1
        assert "DEBIAN_FRONTEND=noninteractive" in env_cmds[0].cmd

    def test_env_object(self):
        pm = PackageManager.get(Distro("debian", "bookworm"))
        assert pm.envs == [Env("DEBIAN_FRONTEND", "noninteractive")]


class TestRockySpecificBehavior:
    def test_includes_config_manager_and_epel(self):
        pm = PackageManager.get(Distro("rocky", "9.3"))
        cmds = pm.docker_commands(["gcc"])
        main_run = [c for c in cmds if c.type == DockerInstruction.RUN][-1]
        assert "config-manager" in main_run.cmd
        assert "epel-release" in main_run.cmd

    def test_upgrade_chain_contains_all_setup_steps(self):
        pm = PackageManager.get(Distro("rocky", "9.3"))
        # upgrade list should have 4 entries: upgrade, install config-manager,
        # enable crb, install epel-release
        assert len(pm.upgrade) == 4
