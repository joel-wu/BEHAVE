"""Exact local tool images, not source-path/TTL caches or symlink flattening."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from behave.isolation import (
    IsolationUnavailable,
    content_attestation,
    content_digest,
    package_image,
    python_package,
    read_only,
    runtime_tree,
)
from behave.isolation.linux import _verify_source


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "batch"))
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = tmp_path / "installed"
    source.mkdir()
    (source / "library.so").write_bytes(b"library bytes")
    (source / "library.so").chmod(0o755)
    (source / "data").write_bytes(b"data bytes")
    return source


def verify(mount):
    _verify_source(mount.source, mount.sha256, allow_symlinks=mount.allow_symlinks,
                   reuse_attestation=mount.reuse_attestation)


@pytest.mark.parametrize("links", [False, True])
def test_tool_image_preserves_full_mount_identity_and_never_follows_links(source, links):
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "data.pyc").write_bytes(b"tool data, not discardable Python bytecode")
    outside = source.parent / "outside"
    outside.mkdir(mode=0o700)
    (outside / "sentinel").write_bytes(b"must not be copied or chmodded")
    if links:
        (source / "relative.so").symlink_to("library.so")
        (source / "directory-link").symlink_to(outside, target_is_directory=True)
        (source / "guest-only-link").symlink_to("/opt/eda/sibling/data")
    direct = read_only(str(source), "/opt/eda/lib", allow_symlinks=links)
    local = runtime_tree(str(source), direct.target, allow_symlinks=links)
    assert local.identity() == direct.identity()
    assert Path(local.source).is_relative_to(source.parent / "batch/images")
    assert (Path(local.source) / "__pycache__/data.pyc").read_bytes() == b"tool data, not discardable Python bytecode"
    assert (Path(local.source) / "library.so").stat().st_mode & 0o777 == 0o555
    assert (Path(local.source) / "data").stat().st_mode & 0o777 == 0o444
    assert outside.stat().st_mode & 0o777 == 0o700
    assert (outside / "sentinel").read_bytes() == b"must not be copied or chmodded"
    if links:
        for name in ("relative.so", "directory-link", "guest-only-link"):
            assert os.readlink(Path(local.source) / name) == os.readlink(source / name)
    verify(local)
    if not links:
        assert python_package(str(source), "/python").sha256 != local.sha256


def test_python_and_no_link_tool_policy_still_refuse_links(source):
    (source / "link").symlink_to("data")
    for freeze in (python_package, runtime_tree):
        with pytest.raises(ValueError, match="symlink"):
            freeze(str(source), "/runtime/tool")


def test_tool_tree_is_reused_within_batch_and_reprepared_after_update(source, monkeypatch):
    first = runtime_tree(str(source), "/tool")
    walked = []
    manifest = content_attestation.metadata_manifest
    def observed(root, **kwargs):
        walked.append(Path(root))
        return manifest(root, **kwargs)
    monkeypatch.setattr(content_attestation, "metadata_manifest", observed)
    with monkeypatch.context() as no_copy:
        no_copy.setattr(package_image, "_copy_tree", lambda *a, **k: pytest.fail("warm tree recopied"))
        assert runtime_tree(str(source), "/tool") == first
    assert walked == []
    verify(first)
    (source / "data").write_bytes(b"installed update")
    assert runtime_tree(str(source), "/tool") == first
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(source.parent / "updated-batch"))
    second = runtime_tree(str(source), "/tool")
    assert second.sha256 != first.sha256
    assert (Path(first.source) / "data").read_bytes() == b"data bytes"
    assert (Path(second.source) / "data").read_bytes() == b"installed update"
    verify(first)
    verify(second)


def test_corrupted_local_tool_tree_is_refused_and_preserved_on_prepare(source, monkeypatch):
    first = runtime_tree(str(source), "/tool")
    leaf = Path(first.source) / "data"
    leaf.chmod(0o644)
    leaf.write_bytes(b"corruption")
    with pytest.raises(IsolationUnavailable) as caught:
        verify(first)
    assert caught.value.refusal.code == "isolation-input-changed"
    with pytest.raises(ValueError, match="corrupt"):
        runtime_tree(str(source), "/other-tool")
    assert leaf.read_bytes() == b"corruption"
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(source.parent / "updated-batch"))
    fresh = runtime_tree(str(source), "/tool")
    assert fresh.source != first.source
    assert fresh.sha256 == first.sha256


def test_preparation_pins_copied_bytes_not_later_installation_edits(source, monkeypatch):
    copy = package_image._copy_tree
    calls = []
    def mutate(*args, **kwargs):
        copy(*args, **kwargs)
        if not calls:
            (source / "data").write_bytes(b"update during copy")
        calls.append(1)
    monkeypatch.setattr(package_image, "_copy_tree", mutate)
    image = runtime_tree(str(source), "/tool")
    assert len(calls) == 1
    assert image.sha256 != content_digest(str(source))
    assert (Path(image.source) / "data").read_bytes() == b"data bytes"
    verify(image)


def test_parallel_tool_freezes_share_one_publication_without_mutable_workspace(source, monkeypatch):
    copy = package_image._copy_tree
    copies = []
    def observed(*args, **kwargs):
        copies.append(1)
        return copy(*args, **kwargs)
    monkeypatch.setattr(package_image, "_copy_tree", observed)
    with ThreadPoolExecutor(max_workers=8) as pool:
        mounts = tuple(pool.map(lambda _: runtime_tree(str(source), "/tool"), range(24)))
    assert len(set(m.source for m in mounts)) == len(copies) == 1
    assert all(m.identity() == mounts[0].identity() for m in mounts)
    assert not list((source.parent / "batch/images").glob(".building-*"))


def test_tool_tree_rejects_special_files_without_publishing_partial_tree(source):
    os.mkfifo(source / "pipe")
    with pytest.raises(ValueError, match="special file"):
        runtime_tree(str(source), "/tool")
    assert not list((source.parent / "batch/images").glob(".building-*"))
