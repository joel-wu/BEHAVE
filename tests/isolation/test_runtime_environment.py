"""Batch preparation is reused; submitted inputs and execution state are not."""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import pytest

from behave.isolation import content_digest, package_image, python_package, read_only
from behave.isolation.model import ReadOnlyMount
from behave.isolation.preparation_control import PreparationCancelled
from behave.isolation.runtime_environment import RuntimeEnvironment


def test_fixed_tree_never_rescans_installation_within_batch(tmp_path, monkeypatch):
    source = tmp_path / "installed"
    source.mkdir()
    (source / "value.py").write_text("OLD = 1")
    batch = RuntimeEnvironment(tmp_path / "batch")
    with batch.activate():
        first = python_package(str(source), "/runtime/demo")
    assert first.prepared_runtime
    (source / "value.py").write_text("NEW = 2")
    original = package_image._content_digest

    def no_walk(*args, **kwargs):
        pytest.fail("a prepared batch walked its runtime again")

    monkeypatch.setattr(package_image, "_content_digest", no_walk)
    # Also reload from disk, as a separate host worker does.
    with RuntimeEnvironment(batch.directory).activate():
        assert python_package(str(source), first.target) == first
    with batch.activate():
        assert python_package(str(source), first.target) is first
    monkeypatch.setattr(package_image, "_content_digest", original)
    with RuntimeEnvironment(tmp_path / "new-batch").activate():
        second = python_package(str(source), first.target)
    assert second.sha256 != first.sha256
    assert (Path(first.source) / "value.py").read_text() == "OLD = 1"


def test_tree_preparation_hashes_the_local_copy_not_the_installation(tmp_path, monkeypatch):
    source = tmp_path / "installed"
    source.mkdir()
    (source / "value.py").write_text("VALUE = 1")
    digest = package_image._content_digest
    scanned = []

    def local_only(path, **kwargs):
        assert Path(path).is_relative_to(tmp_path / "batch/images")
        scanned.append(path)
        return digest(path, **kwargs)

    monkeypatch.setattr(package_image, "_content_digest", local_only)
    with RuntimeEnvironment(tmp_path / "batch").activate():
        image = python_package(str(source), "/runtime/demo")
    assert len(scanned) == 1
    assert image.sha256 == content_digest(image.source)


def test_script_reuse_does_not_read_installed_or_prepared_bytes(tmp_path, monkeypatch):
    source = tmp_path / "wrapper.py"
    source.write_text("print('ready')")
    batch = RuntimeEnvironment(tmp_path / "batch")
    with batch.activate():
        first = package_image.executable_script(str(source), "/tools/wrapper")
    read = Path.read_bytes

    def no_runtime_read(path):
        assert path not in (source, Path(first.source))
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", no_runtime_read)
    with RuntimeEnvironment(batch.directory).activate():
        assert package_image.executable_script(str(source), "/tools/wrapper") == first
    assert first.prepared_runtime


def test_concurrent_preparation_has_one_builder(tmp_path):
    calls = []

    def build():
        calls.append(1)
        return {"version": "fixed"}

    def worker(_):
        # Independent environment objects use the interprocess publication lock.
        return RuntimeEnvironment(tmp_path / "batch").prepare(
            "resource", build, encode=lambda x: x, decode=lambda x: x)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(worker, range(32)))
    assert rows == [{"version": "fixed"}] * 32
    assert calls == [1]


def _process_builder(directory):
    batch = RuntimeEnvironment(directory)

    def build():
        with (batch.directory / "builds").open("a") as stream:
            stream.write("prepared\n")
        return 17

    return batch.prepare("shared", build, encode=lambda x: x, decode=lambda x: x)


def test_processes_share_one_publication(tmp_path):
    batch = tmp_path / "batch"
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        assert list(pool.map(_process_builder, [str(batch)] * 12)) == [17] * 12
    assert (batch / "builds").read_text() == "prepared\n"


def test_cancelled_preparation_does_not_publish(tmp_path):
    batch = RuntimeEnvironment(tmp_path / "batch")

    def cancelled():
        raise PreparationCancelled()

    with pytest.raises(PreparationCancelled):
        batch.prepare("resource", cancelled, encode=lambda x: x, decode=lambda x: x)
    assert not list(batch.directory.glob("*.json"))
    assert batch.prepare("resource", lambda: 7, encode=lambda x: x, decode=lambda x: x) == 7


def test_candidate_mounts_are_not_prepared_runtime(tmp_path):
    source = tmp_path / "candidate.py"
    source.write_text("a = 1")
    with RuntimeEnvironment(tmp_path / "batch").activate():
        first = read_only(str(source), "/inputs/source")
        source.write_text("a = 2")
        second = read_only(str(source), "/inputs/source")
    assert not first.prepared_runtime and not second.prepared_runtime
    assert first.sha256 != second.sha256


def test_stdlib_exclusion_is_not_scanned_or_copied(tmp_path, monkeypatch):
    source = tmp_path / "stdlib"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1")
    (source / "site-packages").mkdir()
    # Special files would make a byte scan fail: this subtree must never be read.
    import os
    os.mkfifo(source / "site-packages" / "not-a-runtime-file")
    with RuntimeEnvironment(tmp_path / "batch").activate():
        image = python_package(str(source), "/stdlib", exclude_directories=("site-packages",))
    assert not (Path(image.source) / "site-packages").exists()
    assert content_digest(image.source) == image.sha256


def test_runtime_file_is_local_and_pinned(tmp_path, monkeypatch):
    source = tmp_path / "tool"
    source.write_bytes(b"first")
    batch = RuntimeEnvironment(tmp_path / "batch")
    with batch.activate():
        first = read_only(str(source), "/tool", reuse_attestation=True)
        source.write_bytes(b"second")
        assert read_only(str(source), "/tool", reuse_attestation=True) == first
    assert first.prepared_runtime
    assert Path(first.source).read_bytes() == b"first"
    assert ReadOnlyMount(**asdict(first)) == first


def _short_lived_image_user(directory, installed):
    """A fresh arm imports isolation after choosing its private scratch root."""
    import importlib
    import os
    import subprocess
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="execution-", dir=Path(directory).parent) as scratch:
        os.environ["BEHAVE_SCRATCH_DIR"] = scratch
        importlib.reload(package_image)
        with RuntimeEnvironment(directory).activate():
            tool = package_image.runtime_file(str(Path(installed) / "tool"), "/tools/check")
            tree = package_image.python_package(installed, "/runtime/example")
        output = subprocess.run([tool.source], check=True, capture_output=True).stdout
        return {"scratch": scratch, "tool": asdict(tool), "tree": asdict(tree), "output": output}


def test_shared_images_outlive_the_first_workers_private_scratch(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    tool = installed / "tool"
    tool.write_text("#!/bin/sh\nprintf ready\n")
    tool.chmod(0o755)
    (installed / "module.py").write_text("VALUE = 1\n")
    directory = tmp_path / "batch"
    results = []
    for _ in range(2):
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            result = pool.submit(_short_lived_image_user, str(directory), str(installed)).result()
        assert not Path(result["scratch"]).exists()
        assert Path(result["tool"]["source"]).is_relative_to(directory / "images")
        assert Path(result["tool"]["source"]).is_file()
        assert Path(result["tree"]["source"]).is_dir()
        assert result["output"] == b"ready"
        results.append(result)
    assert results[0]["tool"] == results[1]["tool"]
    assert results[0]["tree"] == results[1]["tree"]


def test_legacy_worker_local_image_declarations_are_reprepared(tmp_path):
    import hashlib
    import json

    batch = RuntimeEnvironment(tmp_path / "batch")
    key = ["fixed-tool", "check"]
    old_name = hashlib.sha256(json.dumps(["runtime-environment/1", key], sort_keys=True).encode()).hexdigest()
    stale = {"source": str(tmp_path / "deleted-worker/tool")}
    (batch.directory / (old_name + ".json")).write_text(json.dumps(stale))
    built = []

    def build():
        built.append(True)
        return {"source": str(batch.directory / "images/tool")}

    expected = batch.prepare(key, build, encode=lambda value: value, decode=lambda value: value)
    assert built == [True]
    assert expected != stale
    fresh = RuntimeEnvironment(batch.directory)
    assert fresh.prepare(key, build, encode=lambda value: value, decode=lambda value: value) == expected
    assert built == [True]
