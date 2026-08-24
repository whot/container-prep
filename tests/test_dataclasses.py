# SPDX-License-Identifier: MIT
"""Tests for the small dataclasses in container-prep.py."""

import pytest
from container_prep import (
    Distro,
    DockerDistro,
    DockerRegistry,
    Env,
    GithubRepo,
    ImageId,
    ImagePath,
    ImageSuffix,
    ImageTag,
    Platform,
)


class TestEnv:
    def test_str(self):
        e = Env(key="FOO", value="bar")
        assert str(e) == "FOO=bar"

    def test_str_empty_value(self):
        e = Env(key="FOO", value="")
        assert str(e) == "FOO="

    def test_frozen(self):
        e = Env(key="FOO", value="bar")
        with pytest.raises(AttributeError):
            e.key = "BAZ"
        with pytest.raises(AttributeError):
            e.value = "qux"


class TestDistro:
    def test_str(self):
        d = Distro(name="fedora", version="44")
        assert str(d) == "fedora:44"

    def test_frozen(self):
        d = Distro(name="fedora", version="44")
        with pytest.raises(AttributeError):
            d.name = "ubuntu"
        with pytest.raises(AttributeError):
            d.version = "24.04"


class TestGithubRepo:
    def test_simple_path(self):
        r = GithubRepo("owner/repo")
        assert r.namespace == "owner"
        assert r.name == "repo"

    def test_multi_component_path(self):
        r = GithubRepo("ghcr.io/owner/repo")
        assert r.namespace == "ghcr.io/owner"
        assert r.name == "repo"

    def test_equality_same(self):
        a = GithubRepo("owner/repo")
        b = GithubRepo("owner/repo")
        assert a == b

    def test_equality_different(self):
        a = GithubRepo("owner/repo")
        b = GithubRepo("other/repo")
        assert a != b

    def test_str_roundtrips(self):
        path = "owner/repo"
        r = GithubRepo(path)
        assert str(r) == path

    def test_str_roundtrips_multi_component(self):
        path = "ghcr.io/owner/repo"
        r = GithubRepo(path)
        assert str(r) == path


class TestImageId:
    def test_create_distro_tag(self):
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("latest")
        img = ImageId.create(distro=distro, tag=tag)
        assert img.image_id == "fedora/44:latest"

    def test_create_with_platform(self):
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("latest")
        platform = Platform("linux/amd64")
        img = ImageId.create(distro=distro, tag=tag, platform=platform)
        assert img.image_id == "fedora/44/amd64:latest"

    def test_create_with_suffix(self):
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("latest")
        suffix = ImageSuffix("custom/path")
        img = ImageId.create(distro=distro, tag=tag, suffix=suffix)
        assert img.image_id == "custom/path:latest"

    def test_str_matches_image_id(self):
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("v1")
        img = ImageId.create(distro=distro, tag=tag)
        assert str(img) == img.image_id

    def test_tag_property(self):
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("v1")
        img = ImageId.create(distro=distro, tag=tag)
        assert img.tag == tag


class TestImagePath:
    def test_create(self):
        repo = GithubRepo("owner/repo")
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("latest")
        image_id = ImageId.create(distro=distro, tag=tag)
        path = ImagePath.create(repo=repo, image_id=image_id)
        assert path.path == "owner/repo/fedora/44:latest"

    def test_tag_delegates_to_image_id(self):
        repo = GithubRepo("owner/repo")
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("v2")
        image_id = ImageId.create(distro=distro, tag=tag)
        path = ImagePath.create(repo=repo, image_id=image_id)
        assert path.tag == tag

    def test_str_matches_path(self):
        repo = GithubRepo("owner/repo")
        distro = Distro(name="fedora", version="44")
        tag = ImageTag("latest")
        image_id = ImageId.create(distro=distro, tag=tag)
        path = ImagePath.create(repo=repo, image_id=image_id)
        assert str(path) == path.path


class TestDockerDistro:
    def test_str_no_platform_no_registry(self):
        distro = Distro(name="fedora", version="44")
        dd = DockerDistro(distro=distro, platform=None, registry_name=None)
        assert str(dd) == "fedora:44"

    def test_str_with_platform(self):
        distro = Distro(name="fedora", version="44")
        platform = Platform("linux/amd64")
        dd = DockerDistro(distro=distro, platform=platform, registry_name=None)
        assert str(dd) == "--platform=linux/amd64 fedora:44"

    def test_str_with_registry(self):
        distro = Distro(name="fedora", version="44")
        registry = DockerRegistry("registry.example.com")
        dd = DockerDistro(distro=distro, platform=None, registry_name=registry)
        assert str(dd) == "registry.example.com/fedora:44"

    def test_str_with_platform_and_registry(self):
        distro = Distro(name="fedora", version="44")
        platform = Platform("linux/amd64")
        registry = DockerRegistry("registry.example.com")
        dd = DockerDistro(distro=distro, platform=platform, registry_name=registry)
        assert str(dd) == "--platform=linux/amd64 registry.example.com/fedora:44"
