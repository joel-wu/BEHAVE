"""The optimized path walk must emit the original complete metadata image."""

import os
import stat
import threading
from collections import Counter
from pathlib import Path

import pytest

from behave.isolation import content_attestation
from behave.isolation.content_attestation import metadata_manifest


def _path_oracle(root, *, allow_symlinks, exclude_python_bytecode):
    """Original Path-based traversal, independent of production row construction."""
    def row(path):
        info = path.lstat()
        return (
            path.relative_to(root).as_posix(), stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns,
            os.readlink(path) if stat.S_ISLNK(info.st_mode) else None,
        )

    rows = [row(root)]
    if root.is_file():
        return tuple(rows)

    def visit(directory):
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            if exclude_python_bytecode and (
                (child.name == "__pycache__" and child.is_dir())
                or (child.name.endswith((".pyc", ".pyo")) and not child.is_dir())
            ):
                continue
            path = Path(child.path)
            entry = row(path)
            if entry[1] == stat.S_IFLNK and not allow_symlinks:
                raise ValueError("symlink")
            if entry[1] not in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFREG):
                raise ValueError("special file")
            rows.append(entry)
            if entry[1] == stat.S_IFDIR:
                visit(path)

    visit(root)
    return tuple(rows)


@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("links", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_manifest_matches_path_oracle(tmp_path, monkeypatch, filtered, links, relative):
    tree = tmp_path / "runtime"
    tree.mkdir()
    for name in (".hidden", "é/目录", "empty", "__pycache__", "kept.pyc"):
        (tree / name).mkdir(parents=True)
    for name in ("a", "é/目录/x\ty", ".hidden/tool", "z.pyo",
                 "__pycache__/module.pyc", "kept.pyc/data"):
        (tree / name).write_bytes(b"value")
    (tree / ".hidden/tool").chmod(0o755)
    if links:
        (tree / "link").symlink_to("é")
        (tree / "broken").symlink_to("missing")
    if relative:
        monkeypatch.chdir(tmp_path)
        tree = Path("runtime")
    arguments = {"allow_symlinks": links, "exclude_python_bytecode": filtered}
    assert metadata_manifest(tree, **arguments) == _path_oracle(tree, **arguments)


@pytest.mark.parametrize("kind", ["symlink-root", "symlink-child", "fifo-root", "fifo-child"])
def test_manifest_rejects_unpermitted_objects(tmp_path, kind):
    tree = tmp_path / "runtime"
    if kind.endswith("child"):
        tree.mkdir()
        item = tree / "child"
    else:
        item = tree
    if kind.startswith("symlink"):
        item.symlink_to("missing")
    else:
        os.mkfifo(item)
    with pytest.raises(ValueError):
        metadata_manifest(tree, allow_symlinks=False)


def test_manifest_retains_all_metadata_and_fresh_descendant_checks(tmp_path):
    tree = tmp_path / "runtime"
    tree.mkdir()
    leaf = tree / "data"
    leaf.write_bytes(b"a")
    arguments = {"allow_symlinks": True, "exclude_python_bytecode": False}
    previous = metadata_manifest(tree, **arguments)
    for edit in (
        lambda: leaf.write_bytes(b"replacement"),
        lambda: leaf.chmod(0o755),
        lambda: leaf.rename(tree / "renamed"),
        lambda: (tree / "new").symlink_to("renamed"),
        lambda: (tree / "renamed").unlink(),
    ):
        edit()
        actual = metadata_manifest(tree, **arguments)
        assert actual == _path_oracle(tree, **arguments)
        assert actual != previous
        previous = actual


def test_parallel_manifest_keeps_depth_first_order_and_checks_every_leaf(tmp_path, monkeypatch):
    tree = tmp_path / "runtime"
    for directory in ("a", "a.", "b/deep", "c", "d"):
        (tree / directory).mkdir(parents=True)
        for index in range(4):
            (tree / directory / str(index)).write_bytes(b"data")
    oracle = _path_oracle(tree, allow_symlinks=False, exclude_python_bytecode=False)
    original = os.lstat
    calls, lock = Counter(), threading.Lock()
    # Force two directory scans to overlap without relying on wall-clock speed.
    rendezvous = threading.Barrier(2, timeout=5)
    met = set()

    def fresh_stat(path, *args, **kwargs):
        name = str(path)
        with lock:
            calls[name] += 1
            synchronize = name in {str(tree / "c/0"), str(tree / "d/0")} and name not in met
            met.add(name)
        if synchronize:
            rendezvous.wait()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(content_attestation.os, "lstat", fresh_stat)
    assert metadata_manifest(tree, allow_symlinks=False) == oracle
    assert calls == Counter({str(tree if row[0] == "." else tree / row[0]): 1
                             for row in oracle})
    # A second invocation must check the descendants again, even though its
    # sorted image is unchanged. There is no time-based metadata reuse.
    assert metadata_manifest(tree, allow_symlinks=False) == oracle
    assert set(calls.values()) == {2}


def test_parallel_manifest_propagates_nested_failure_and_can_run_again(tmp_path):
    tree = tmp_path / "runtime"
    for name in ("a", "b", "c"):
        (tree / name).mkdir(parents=True)
        (tree / name / "data").write_bytes(b"safe")
    bad = tree / "b/link"
    bad.symlink_to("missing")
    with pytest.raises(ValueError, match="symlink"):
        metadata_manifest(tree, allow_symlinks=False)
    bad.unlink()
    assert metadata_manifest(tree, allow_symlinks=False) == _path_oracle(
        tree, allow_symlinks=False, exclude_python_bytecode=False)
