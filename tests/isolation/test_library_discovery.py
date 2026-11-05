"""Native isolation dependencies are discoverable without spawning helpers."""

import os
from pathlib import Path

import pytest

from behave.isolation import linux


@pytest.mark.parametrize("multiarch, directory", [
    ("x86_64-linux-gnu", "/lib/x86_64-linux-gnu"),
    ("x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu"),
    ("aarch64-linux-gnu", "/lib/aarch64-linux-gnu"),
    ("aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu"),
    (None, "/lib64"), (None, "/usr/lib64"), (None, "/lib"), (None, "/usr/lib"),
])
def test_standard_library_needs_no_fork(monkeypatch, multiarch, directory):
    expected = Path(directory) / "libseccomp.so.2"
    monkeypatch.setattr(linux.sysconfig, "get_config_var", lambda key: multiarch)
    monkeypatch.setattr(Path, "is_file", lambda path: path == expected)
    monkeypatch.setattr(linux.ctypes.util, "find_library", lambda *_: pytest.fail("must not spawn discovery"))
    monkeypatch.setattr(linux.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not fork"))
    assert linux._seccomp_path() == os.path.realpath(expected)


def test_multiarch_library_precedes_generic_directories(monkeypatch):
    expected = Path("/usr/lib/aarch64-linux-gnu/libseccomp.so.2")
    monkeypatch.setattr(linux.sysconfig, "get_config_var", lambda key: "aarch64-linux-gnu")
    monkeypatch.setattr(Path, "is_file", lambda path: path in {expected, Path("/lib64/libseccomp.so.2")})
    assert linux._seccomp_path() == os.path.realpath(expected)


def test_platform_discovery_retains_alternate_library_names(monkeypatch):
    expected = Path("/usr/lib64/libseccomp.so.fixture")
    monkeypatch.setattr(linux.sysconfig, "get_config_var", lambda key: None)
    monkeypatch.setattr(Path, "is_file", lambda path: path == expected)
    calls = []
    monkeypatch.setattr(linux.ctypes.util, "find_library", lambda name: calls.append(name) or expected.name)
    assert linux._seccomp_path() == os.path.realpath(expected)
    assert calls == ["seccomp"]


def test_discovered_absolute_library_resolves_symlink(tmp_path, monkeypatch):
    library = tmp_path / "libseccomp.so.fixture"
    library.write_bytes(b"fixture")
    link = tmp_path / "libseccomp.so"
    link.symlink_to(library)
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == link and is_file(path))
    monkeypatch.setattr(linux.ctypes.util, "find_library", lambda name: str(link))
    assert linux._seccomp_path() == str(library)


@pytest.mark.parametrize("discovered", [None, "libseccomp.so.missing"])
def test_missing_library_still_refuses(monkeypatch, discovered):
    monkeypatch.setattr(Path, "is_file", lambda path: False)
    monkeypatch.setattr(linux.ctypes.util, "find_library", lambda name: discovered)
    assert linux._seccomp_path() is None
