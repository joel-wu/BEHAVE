import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools.packed_object_cache import hydrate, reference_sizes


def blob(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def packed(path, members):
    with tarfile.open(path, "w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def test_selected_objects_only_and_no_cold_writes(tmp_path):
    data = b"candidate tokens and score"
    ref = blob(data)
    source = packed(tmp_path / "cold.tar", [("unrelated", b"ignore"),
                    ("a/objects/" + ref["sha256"], data)])
    before = source.read_bytes()
    result = hydrate([source], [ref], tmp_path / "hot")
    assert (tmp_path / "hot/objects" / ref["sha256"]).read_bytes() == data
    assert result["objects"] == 1
    assert result["legacy_reads"] == result["cold_small_file_writes"] == 0
    assert result["training_readiness_published"] is False
    assert source.read_bytes() == before


def test_paths_are_not_extracted(tmp_path):
    data = b"safe"
    ref = blob(data)
    source = packed(tmp_path / "cold.tar", [("../../outside/" + ref["sha256"], data)])
    hydrate([source], [ref], tmp_path / "hot")
    assert not (tmp_path / "outside").exists()
    assert (tmp_path / "hot/objects" / ref["sha256"]).read_bytes() == data


@pytest.mark.parametrize("payload", [b"wrong", b"longer"])
def test_bad_content_never_commits_cache(tmp_path, payload):
    ref = blob(b"right")
    source = packed(tmp_path / "cold.tar", [(ref["sha256"], payload)])
    with pytest.raises(ValueError, match="differs"):
        hydrate([source], [ref], tmp_path / "hot")
    assert not (tmp_path / "hot/cache.json").exists()
    assert not (tmp_path / "hot/objects" / ref["sha256"]).exists()


def test_links_never_follow_external_paths(tmp_path):
    ref = blob(b"secret")
    source = tmp_path / "cold.tar"
    with tarfile.open(source, "w") as archive:
        info = tarfile.TarInfo(ref["sha256"])
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(ValueError, match="lack"):
        hydrate([source], [ref], tmp_path / "hot")


def test_cross_archive_and_duplicates(tmp_path):
    a, b = blob(b"one"), blob(b"two")
    first = packed(tmp_path / "a.tar", [(a["sha256"], b"one")])
    second = packed(tmp_path / "b.tar", [(a["sha256"], b"one"), (b["sha256"], b"two")])
    assert hydrate([first, second], [a, b, a], tmp_path / "hot")["objects"] == 2


def test_refuses_existing_or_nonlocal_destination(tmp_path):
    ref = blob(b"one")
    source = packed(tmp_path / "a.tar", [(ref["sha256"], b"one")])
    with pytest.raises(FileExistsError):
        hydrate([source], [ref], tmp_path)
    with pytest.raises(ValueError, match="scratch"):
        hydrate([source], [ref], Path("/store/never-write"))


def test_incomplete_cache_has_no_success_receipt(tmp_path):
    source = packed(tmp_path / "empty.tar", [])
    with pytest.raises(ValueError, match="lack 1"):
        hydrate([source], [blob(b"missing")], tmp_path / "hot")
    assert json.loads((tmp_path / "hot/failure.json").read_text())["verified_objects"] == 0
    assert not (tmp_path / "hot/cache.json").exists()


def test_conflicting_reference_sizes():
    ref = blob(b"one")
    with pytest.raises(ValueError, match="conflicting"):
        reference_sizes([ref, {**ref, "size_bytes": 9}])
