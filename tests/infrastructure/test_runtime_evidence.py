"""Closed-run copying preserves records and keeps cleanup with the caller."""

from pathlib import Path

import pytest

from behave.runtime import records


def test_interrupted_copy_retains_source_and_reuses_completed_files(tmp_path, monkeypatch):
    source, target = tmp_path / "local", tmp_path / "archive"
    source.mkdir()
    (source / "a.json").write_bytes(b"original a")
    (source / "b.json").write_bytes(b"original b")
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    copied, copy = [], records.shutil.copy2

    def interrupt(src, dst):
        if copied:
            raise OSError("fixture copy interrupted")
        copied.append(Path(src).name)
        return copy(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(records.shutil, "copy2", interrupt)
        with pytest.raises(records.shutil.Error, match="copy interrupted"):
            records.archive_run(source, target)
    kept = target / copied[0]
    stat = kept.stat()
    assert kept.read_bytes() == before[kept.name]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
    assert records.archive_run(source, target) == target
    assert kept.stat().st_ino == stat.st_ino and kept.stat().st_mtime_ns == stat.st_mtime_ns
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
