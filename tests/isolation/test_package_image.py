from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from behave.isolation import (
    content_attestation,
    content_digest,
    executable_script,
    package_image,
    python_package,
    read_only,
)
from behave.isolation.linux import _verify_source


@pytest.fixture(autouse=True)
def runtime_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "batch"))


def _package(root: Path) -> Path:
    source = root / "demo"
    source.mkdir()
    (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-311.pyc").write_bytes(b"first")
    return source


def test_package_image_ignores_bytecode_but_tracks_source(tmp_path, monkeypatch):
    source = _package(tmp_path)

    first = python_package(str(source), "/runtime/demo")
    assert not (Path(first.source) / "__pycache__").exists()

    (source / "__pycache__" / "__init__.cpython-311.pyc").write_bytes(
        b"replacement")
    same = python_package(str(source), "/runtime/demo")
    assert same.source == first.source
    assert same.sha256 == first.sha256

    (source / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert python_package(str(source), "/runtime/demo") == first
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-batch"))
    changed = python_package(str(source), "/runtime/demo")
    assert changed.source != first.source
    assert changed.sha256 != first.sha256


def test_warm_package_image_reuses_prepared_content_without_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = _package(tmp_path)
    first = python_package(str(source), "/runtime/demo")
    scanned = []
    digest = package_image._content_digest

    def observed(root, **kwargs):
        scanned.append(root)
        return digest(root, **kwargs)

    monkeypatch.setattr(package_image, "_content_digest", observed)
    second = python_package(str(source), "/runtime/demo")
    assert second.sha256 == first.sha256
    assert scanned == []


def test_warm_image_reuses_mount_and_explicit_verification_still_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = _package(tmp_path)
    first = python_package(str(source), "/runtime/demo")
    visited = []
    original = content_attestation.metadata_manifest

    def observed(root, **kwargs):
        visited.append(Path(root))
        return original(root, **kwargs)

    monkeypatch.setattr(content_attestation, "metadata_manifest", observed)
    same = python_package(str(source), "/runtime/demo")
    assert same == first
    assert visited == []
    _verify_source(same.source, same.sha256, reuse_attestation=True)
    assert set(visited) <= {Path(first.source)}


def test_corrupt_package_image_cannot_supply_new_mount_identity(tmp_path, monkeypatch):
    source = _package(tmp_path)
    original = python_package(str(source), "/runtime/demo")
    leaf = Path(original.source) / "__init__.py"
    leaf.chmod(0o644)
    leaf.write_text("VALUE = 9\n")
    # A distinct mount declaration in this batch must not certify corruption.
    with pytest.raises(ValueError, match="corrupt"):
        python_package(str(source), "/runtime/other")
    assert leaf.read_text() == "VALUE = 9\n"
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-batch"))
    fresh = python_package(str(source), "/runtime/demo")
    assert fresh.source != original.source
    assert fresh.sha256 == original.sha256


def test_bytecode_filtered_attestation_cannot_certify_a_full_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = _package(tmp_path)
    image = python_package(str(source), "/runtime/demo")
    full = read_only(str(source), "/source", reuse_attestation=True)
    assert full.sha256 == content_digest(str(source))
    assert full.sha256 != image.sha256
    content_attestation._MEMORY.clear()
    assert python_package(str(source), "/runtime/demo").sha256 == image.sha256
    assert read_only(str(source), "/source", reuse_attestation=True).sha256 == full.sha256


def test_package_filter_preserves_non_bytecode_entries_with_similar_names(tmp_path, monkeypatch):
    source = tmp_path / "demo"
    source.mkdir()
    (source / "__pycache__").write_bytes(b"ordinary file")
    (source / "data.pyc").mkdir()
    (source / "data.pyc" / "value").write_bytes(b"ordinary directory")
    image = Path(python_package(str(source), "/runtime/demo").source)
    assert (image / "__pycache__").read_bytes() == b"ordinary file"
    assert (image / "data.pyc" / "value").read_bytes() == b"ordinary directory"


def test_concurrent_package_freezes_ignore_live_bytecode_writes(
    tmp_path, monkeypatch,
):
    source = _package(tmp_path)
    bytecode = source / "__pycache__" / "__init__.cpython-311.pyc"
    stop = threading.Event()

    def rewrite_bytecode():
        index = 0
        while not stop.is_set():
            bytecode.write_bytes(f"cache-{index}".encode("ascii"))
            index += 1

    writer = threading.Thread(target=rewrite_bytecode)
    writer.start()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            mounts = tuple(pool.map(
                lambda _index: python_package(str(source), "/runtime/demo"),
                range(24),
            ))
    finally:
        stop.set()
        writer.join()

    assert len({mount.source for mount in mounts}) == 1
    assert len({mount.sha256 for mount in mounts}) == 1
    for mount in mounts:
        _verify_source(
            mount.source,
            mount.sha256,
            reuse_attestation=mount.reuse_attestation,
        )


def test_runtime_script_does_not_depend_on_package_install_permissions(tmp_path, monkeypatch):
    source = tmp_path / "wrapper.py"
    source.write_bytes(b"#!/runtime/python/bin/python\nprint('ready')\n")
    source.chmod(0o444)
    wheel = executable_script(str(source), "/tools/reader")
    assert source.stat().st_mode & 0o777 == 0o444
    assert Path(wheel.source).read_bytes() == source.read_bytes()
    assert Path(wheel.source).stat().st_mode & 0o777 == 0o555

    source.chmod(0o755)
    editable = executable_script(str(source), "/tools/reader")
    assert editable.sha256 == wheel.sha256
    assert editable.source == wheel.source
    _verify_source(wheel.source, wheel.sha256, executable=True)

    source.write_bytes(b"#!/runtime/python/bin/python\nprint('changed')\n")
    assert executable_script(str(source), "/tools/reader") == wheel
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-batch"))
    changed = executable_script(str(source), "/tools/reader")
    assert changed.sha256 != wheel.sha256


def test_runtime_script_does_not_silently_repair_a_corrupted_image(tmp_path, monkeypatch):
    source = tmp_path / "wrapper.py"
    source.write_bytes(b"#!/runtime/python/bin/python\nprint('ready')\n")
    original = executable_script(str(source), "/tools/reader")
    poisoned = Path(original.source)
    poisoned.chmod(0o755)
    poisoned.write_bytes(b"wrong runtime")
    with pytest.raises(ValueError, match="corrupt"):
        executable_script(str(source), "/tools/another-reader")
    assert poisoned.read_bytes() == b"wrong runtime"
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-batch"))
    fresh = executable_script(str(source), "/tools/reader")
    assert fresh.source != original.source
    assert fresh.sha256 == original.sha256


def test_concurrent_script_publication_preserves_one_byte_identity(tmp_path, monkeypatch):
    source = tmp_path / "wrapper.py"
    source.write_bytes(b"#!/runtime/python/bin/python\nprint('ready')\n")
    source.chmod(0o444)
    with ThreadPoolExecutor(max_workers=8) as pool:
        scripts = tuple(pool.map(
            lambda _index: executable_script(str(source), "/tools/reader"), range(16)))
    assert len({script.source for script in scripts}) == 1
    assert len({script.sha256 for script in scripts}) == 1
    assert source.stat().st_mode & 0o777 == 0o444
