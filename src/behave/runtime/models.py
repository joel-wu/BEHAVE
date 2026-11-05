"""Publish immutable HF directories as whole cold tar objects, retaining hot data.

Call only for a committed checkpoint. Readers must require the verified receipt;
an existing tar alone is not publication. No runtime model path is changed.
"""

import fcntl
import hashlib
import json
import os
import shutil
import socket
import stat
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, FileRef, relative_path
from behave.core.file_input import read_regular_file
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, fields
from behave.runtime.records import write_json

BLOCK = 8 * 1024**2


def marker(source):
    source = Path(source).resolve()
    return source.parent / (source.name + ".cold.json")


def _write(path, value):
    data = canonical_json(value)
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".pending", delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            f.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _lock(source):
    path = marker(source).with_suffix(".lock")
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


@contextmanager
def _publication_lock(destination, key, directory):
    # Hot copies can have different paths (and different source locks), but
    # must serialize the receipt check, tar write and commit for one cold key.
    directory = destination if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".{key}.publication.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("publication lock must be a regular file")
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield
    # Never unlink a lock: another publisher may already be waiting on its inode.


_TIMESTAMP_TICK_NS = 20_000_000


def _identity(path):
    for _ in range(3):
        s = path.lstat()
        if not stat.S_ISREG(s.st_mode):
            raise ValueError("model member is not regular")
        age = time.time_ns() - max(s.st_mtime_ns, s.st_ctime_ns)
        if not 0 <= age < _TIMESTAMP_TICK_NS:
            break
        time.sleep((_TIMESTAMP_TICK_NS - age) / 1e9)
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


@cache
def _transport_host():
    # A recycled node name must not grant cleanup authority over another boot's
    # /tmp files, even if device/inode numbers happen to be reused.
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        # Non-Linux or hidden /proc: private ownership for this process only.
        # Later processes retain unknown packets rather than guessing a boot ID.
        boot = uuid4().hex
    return socket.gethostname() + ":" + boot


def cold_model(source):
    path = marker(source)
    if not path.is_file():
        return None
    row = json.loads(path.read_text())
    if row["hot_source"] != str(Path(source).resolve()) or row["state"] != "verified":
        raise ValueError("cold model receipt does not match hot path")
    return row


def digest(path):
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb", buffering=BLOCK) as f:
        while block := f.read(BLOCK):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def publish_model(source, files, *, checkpoint, staging, destination, lock_directory=None):
    """Publish a verified cold copy, committing its receipt only after readback.

    An interrupted publication starts over: the local packet is rebuilt and the
    cold archive is rewritten under its final name, which readers ignore until
    the receipt exists. Committed receipts and archives are never overwritten.

    All publishers of a destination must share the same lock_directory on a
    filesystem with cross-process/host flock semantics. It defaults to the cold
    destination; use shared NFS instead when cold storage lacks those semantics.
    A node-private directory is not a multi-node publication lock.
    """
    source = Path(source)
    staging, destination = (Path(p).absolute() for p in (staging, destination))
    if not source.is_absolute() or source != source.resolve():
        raise ValueError("need an explicit hot model directory")
    if (staging.resolve() == destination.resolve()
            or any(p.resolve().is_relative_to(source) for p in (staging, destination))):
        raise ValueError("model, staging and cold destination must be separate")
    if lock_directory is not None:
        lock_directory = Path(lock_directory).absolute()
        if lock_directory.resolve().is_relative_to(source):
            raise ValueError("publication locks must be outside the model directory")
    names = [row["path"] for row in files]
    if not names or len(set(names)) != len(names):
        raise ValueError("empty/duplicate model file inventory")
    for row in files:
        p = Path(row["path"])
        full = source / p
        if (p.is_absolute() or ".." in p.parts or full.resolve() != full or not full.is_file()
                or p.as_posix() != row["path"]):
            raise ValueError("unsafe/nonregular model member")
        if full.stat().st_size != row["size_bytes"]:
            raise ValueError("model file size changed")
    identity = {"schema": "behave.cold-hf-archive/1", "checkpoint": checkpoint, "files": files}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    key = hashlib.sha256(encoded).hexdigest()
    with _lock(source), _publication_lock(destination, key, lock_directory):
        return _publish_locked(source, identity, key, staging, destination)


def _publish_locked(source, identity, key, staging, destination):
    files = identity["files"]
    hot_before = {row["path"]: _identity(source / row["path"]) for row in files}
    staging.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    receipt = destination / f"{key}.json"
    archive = destination / f"{key}.tar"
    local = staging / f"{key}.tar"
    if receipt.exists():
        _identity(receipt)
        saved = json.loads(receipt.read_text())
        if (saved["identity"] != identity or saved["state"] != "verified"
                or saved["archive"] != str(archive) or saved["hot_source"] != str(source)):
            raise ValueError("cold receipt differs")
        if _identity(archive)[2] != saved["bytes"]:
            raise ValueError("committed cold archive size differs; hot model retained")
        if "retained_identities" in saved:
            _retained_model(source, saved)
        _sync_directory(destination)
        local.unlink(missing_ok=True)
        return saved
    if receipt.is_symlink():
        raise ValueError("unsafe cold receipt")
    with _new_file(local) as stream:
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for row in files:
                path = source / row["path"]
                before = _identity(path)
                tar.add(path, arcname="model/" + row["path"], recursive=False)
                if before != _identity(path):
                    raise ValueError("hot model changed while packing")
    sha, size = digest(local)
    # Cold storage need not support renaming or hardlinking a weight-sized object,
    # so the archive is written under its final name and committed by the receipt.
    with _new_file(archive) as dst, local.open("rb", buffering=BLOCK) as src:
        shutil.copyfileobj(src, dst, length=BLOCK)
    cold = _identity(archive)
    if digest(archive) != (sha, size) or _identity(archive) != cold:
        raise ValueError("cold archive readback mismatch; hot model retained")
    if hot_before != {row["path"]: _identity(source / row["path"]) for row in files}:
        raise ValueError("hot model changed during cold publication")
    result = {
        "state": "verified",
        "identity": identity,
        "archive": str(archive),
        "sha256": sha,
        "bytes": size,
        "hot_source": str(source),
        "hot_source_retained": True,
        "hot_identities": hot_before,
        "retained_identities": {str(archive): cold},
        "restore": "read the whole tar to local staging, verify SHA256, then extract model/",
    }
    _sync_directory(destination)
    _write(receipt, result)
    local.unlink()
    return result


@contextmanager
def _new_file(path):
    """Replace an earlier attempt's uncommitted file with a fresh one, fsynced on success."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb", buffering=BLOCK) as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def _retained_model(source, receipt):
    """Check explicit retained objects, never enumerate the cold storage tree.

    New publications bind their readback hash to file identities. Legacy
    receipts lack that binding, so verify their payload once before recording
    the identities in the local eviction marker. Changed identities fail closed
    rather than initiating an implicit heavyweight revalidation.
    """
    if receipt.get("retained_directory"):
        cold = Path(receipt["retained_directory"])
        files = receipt["identity"]["files"]
        hashes = receipt["sha256_members"]
        if hashes.keys() != {row["path"] for row in files}:
            raise ValueError("retained model hash inventory differs")
        objects = {str(cold / row["path"]): (hashes[row["path"]], row["size_bytes"]) for row in files}
    else:
        objects = {receipt["archive"]: (receipt["sha256"], receipt["bytes"])}
    saved = receipt.get("retained_identities")
    if saved is not None and saved.keys() != objects.keys():
        raise ValueError("retained model identity inventory differs")
    verified = {}
    for name, expected in objects.items():
        path = Path(name)
        if (not path.is_absolute() or path.resolve() != path or path.is_relative_to(source)
                or ".." in path.parts):
            raise ValueError("unsafe retained model path")
        before = _identity(path)
        if before[2] != expected[1]:
            raise ValueError("retained model size changed; hot model retained")
        if saved is not None:
            if before != saved[name]:
                raise ValueError("retained model changed; hot model retained")
        elif digest(path) != expected or _identity(path) != before:
            raise ValueError("retained model verification failed; hot model retained")
        verified[name] = before
    return verified


def evict_model(source, receipt, *, protected=()):
    """Release only committed, verified, unchanged inventoried hot files.

    The caller fences active consumers and supplies all live comparison/resume
    parents. A durable external receipt survives interruption and permits an
    exact-file retry. Cold objects must still match their verification evidence.
    Never deletes cold objects, run records, or unknown files.
    """
    source = Path(source).absolute()
    if source != source.resolve() or not source.parent.is_dir():
        raise ValueError("unsafe hot model path")
    if source in {Path(p).resolve() for p in protected}:
        return False
    if receipt.get("state") != "verified" or receipt["hot_source"] != str(source):
        raise ValueError("unverified model")
    files = receipt["identity"]["files"]
    identities = receipt.get("hot_identities")
    if not identities:
        raise ValueError("legacy receipt requires hot-content verification before eviction")
    expected = {row["path"]: row["size_bytes"] for row in files}
    if not expected or len(expected) != len(files):
        raise ValueError("empty/duplicate model inventory")
    for name in expected:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or name in ("", ".") or p.as_posix() != name:
            raise ValueError("unsafe model member")
    with _lock(source):
        prior = cold_model(source)
        binding = ("identity", "archive", "sha256", "bytes", "retained_directory", "sha256_members")
        if prior is not None and any(prior.get(key) != receipt.get(key) for key in binding):
            raise ValueError("cold identity changed")
        receipt = dict(receipt)
        if prior is not None and "retained_identities" in prior:
            if ("retained_identities" in receipt
                    and receipt["retained_identities"] != prior["retained_identities"]):
                raise ValueError("retained model identity changed")
            receipt["retained_identities"] = prior["retained_identities"]
        actual = {}
        if source.exists():
            for path in source.rglob("*"):
                if path.is_symlink():
                    raise ValueError("linked hot model member")
                if path.is_file():
                    actual[path.relative_to(source).as_posix()] = _identity(path)
        if actual.keys() - expected.keys() or (prior is None and actual.keys() != expected.keys()):
            raise ValueError("hot model inventory differs")
        if any(value != identities[name] for name, value in actual.items()):
            raise ValueError("hot model changed")
        receipt["retained_identities"] = _retained_model(source, receipt)
        _write(marker(source), {**receipt, "hot_source_retained": False})
        for name in expected:
            path = source / name
            if not path.exists():
                continue
            if _identity(path) != identities[name]:
                raise ValueError("hot model changed before unlink")
            cold = (Path(receipt["retained_directory"]) / name if receipt.get("retained_directory")
                    else Path(receipt["archive"]))
            if cold.resolve() != cold or _identity(cold) != receipt["retained_identities"][str(cold)]:
                raise ValueError("retained model changed before unlink; hot model retained")
            path.unlink()
        directories = {source}
        for name in expected:
            directories.update(source / p for p in Path(name).parents if p != Path("."))
        for p in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            if p.exists():
                p.rmdir()
    return True


def restore_model(source, *, destination=None):
    """Read one cold tar sequentially; publish each member only after SHA check.

    A destination stages into the caller's private scratch, without restoring
    or modifying the NFS hot path. The default retains the training interface.
    Corruption/traversal never exposes a usable partial model at either path.
    """
    source = Path(source).resolve()
    receipt = cold_model(source)
    if receipt is None:
        if destination is None:
            return source
        import shutil

        return Path(shutil.copytree(source, destination))
    if destination is not None:
        target = Path(destination).absolute()
        if target == source or target.exists() or target.is_symlink():
            raise ValueError("cold staging requires a new private destination")
        return _restore_packet(receipt, target)
    with _lock(source):
        files = receipt["identity"]["files"]
        expected = {r["path"]: r["size_bytes"] for r in files}
        if source.is_dir() and all(
            (source / n).is_file() and (source / n).stat().st_size == s for n, s in expected.items()
        ):
            return source
        _restore_packet(receipt, source)
        _write(
            marker(source),
            {**receipt, "hot_source_retained": True, "hot_identities": {n: _identity(source / n) for n in expected}},
        )
        return source


def _restore_packet(receipt, target):
    """One sequential Store read, verified before publishing a local model."""
    if receipt.get("retained_directory"):
        return _restore_directory(receipt, target)
    import shutil

    from behave.core.lifecycle import CleanupState
    from behave.isolation.scratch import ScratchDirectory
    from behave.runtime.models import CheckpointFile

    files = [CheckpointFile.from_dict(row) for row in receipt["identity"]["files"]]
    expected = {row.path: row.size_bytes for row in files}
    if not expected or len(expected) != len(files):
        raise ValueError("invalid cold model inventory")
    if target.exists() or target.is_symlink():
        raise ValueError("incomplete hot model requires explicit recovery")
    if shutil.disk_usage(target.parent).free < receipt["bytes"] + sum(expected.values()):
        raise OSError("insufficient local space for cold model packet and weights")
    with ScratchDirectory(
        prefix="behave-hf-restore-", parent=target.parent, cleanup=lambda: CleanupState.COMPLETE
    ) as staging:
        packet = staging / "archive.tar"
        sha = hashlib.sha256()
        size = 0
        with Path(receipt["archive"]).open("rb", buffering=BLOCK) as src, packet.open("xb", buffering=BLOCK) as dst:
            while block := src.read(BLOCK):
                dst.write(block)
                sha.update(block)
                size += len(block)
            dst.flush()
            os.fsync(dst.fileno())
        if (sha.hexdigest(), size) != (receipt["sha256"], receipt["bytes"]):
            raise ValueError("cold model SHA mismatch")
        model = staging / "model"
        model.mkdir()
        seen = set()
        with tarfile.open(packet, "r:") as tar:
            for member in tar:
                p = Path(member.name)
                if not member.isfile() or p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "model":
                    raise ValueError("unsafe cold model tar member")
                name = p.relative_to("model").as_posix()
                if name not in expected or name in seen or member.size != expected[name]:
                    raise ValueError("cold tar inventory differs")
                member_path = model / name
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, member_path.open("xb") as dst:
                    while block := src.read(BLOCK):
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                seen.add(name)
        if seen != expected.keys():
            raise ValueError("incomplete cold model")
        if target.exists() or target.is_symlink():
            raise ValueError("cold staging destination appeared")
        model.rename(target)
    return target


def _restore_directory(receipt, target):
    """Stage an already verified standalone cold HF into private scratch.

    Older imported HFs already have permanent cold shards. Retaining their
    import proofs avoids another archive copy solely to evict an NFS duplicate.
    This path reads each large shard once and verifies its original digest.
    """
    import shutil

    from behave.core.lifecycle import CleanupState
    from behave.isolation.scratch import ScratchDirectory

    target = Path(target)
    cold = Path(receipt["retained_directory"])
    if not cold.is_absolute() or cold.resolve() != cold or target.exists() or target.is_symlink():
        raise ValueError("unsafe cold directory staging")
    rows = receipt["identity"]["files"]
    hashes = receipt["sha256_members"]
    expected = {row["path"]: row["size_bytes"] for row in rows}
    if not expected or len(expected) != len(rows) or hashes.keys() != expected.keys():
        raise ValueError("invalid cold directory inventory")
    for name in expected:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or name in ("", "."):
            raise ValueError("unsafe cold directory member")
    if shutil.disk_usage(target.parent).free < sum(expected.values()):
        raise OSError("insufficient staging space")
    with ScratchDirectory(
        prefix="behave-hf-directory-", parent=target.parent, cleanup=lambda: CleanupState.COMPLETE
    ) as staging:
        model = staging / "model"
        model.mkdir()
        for name, size in expected.items():
            source = cold / name
            before = _identity(source)
            if source.resolve() != source or before[2] != size:
                raise ValueError("cold member changed")
            dest = model / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest_value = hashlib.sha256()
            count = 0
            with source.open("rb", buffering=BLOCK) as src, dest.open("xb", buffering=BLOCK) as dst:
                while block := src.read(BLOCK):
                    dst.write(block)
                    digest_value.update(block)
                    count += len(block)
                dst.flush()
                os.fsync(dst.fileno())
            if count != size or digest_value.hexdigest() != hashes[name] or _identity(source) != before:
                raise ValueError("cold HF directory content differs from verified import")
        if target.exists() or target.is_symlink():
            raise ValueError("staging destination appeared")
        model.rename(target)
    return target


def _directory(path):
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError("model staging requires an explicit unlinked directory")
    info = path.stat()
    return [info.st_dev, info.st_ino]


def _inventory(root):
    """List only this selected model tree, never the surrounding archive."""
    directories, files = {".": _directory(root)}, []

    def failed(error):
        raise error

    for parent, children, names in os.walk(root, followlinks=False, onerror=failed):
        for name in children:
            path = Path(parent) / name
            directories[path.relative_to(root).as_posix()] = _directory(path)
        files.extend((Path(parent) / name).relative_to(root).as_posix() for name in names)
    return directories, sorted(files)


def _copy(source, target):
    before = _identity(source)
    sha, size = hashlib.sha256(), 0
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb", buffering=BLOCK) as src, target.open("xb", buffering=BLOCK) as dst:
        opened = os.fstat(src.fileno())
        if [opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns] != before:
            raise ValueError("retained model changed before copy")
        while block := src.read(BLOCK):
            dst.write(block)
            sha.update(block)
            size += len(block)
        dst.flush()
        os.fsync(dst.fileno())
    if _identity(source) != before or size != before[2] or digest(target) != (sha.hexdigest(), size):
        raise ValueError("staged model verification failed")
    return {"source": str(source), "source_identity": before, "local_identity": _identity(target),
            "sha256": sha.hexdigest(), "bytes": size}


def stage_model(source, destination, *, receipt):
    """Copy once from retained storage; verify readback only on private scratch.

    File aliases in a model snapshot are copied from their resolved regular
    targets. Directory aliases inside the tree are rejected. The caller must
    supply a new private destination and a receipt outside both model trees.
    """
    source, destination, receipt = Path(source).resolve(), Path(destination).absolute(), Path(receipt).absolute()
    _directory(destination.parent)
    if receipt.resolve() != receipt:
        raise ValueError("model staging receipt requires an unlinked path")
    if (source == destination or source.is_relative_to(destination) or destination.is_relative_to(source)
            or receipt.is_relative_to(destination) or receipt.is_relative_to(source)):
        raise ValueError("model staging source, destination and receipt must be separate")
    if receipt.exists() or receipt.is_symlink():
        raise ValueError("model staging receipt already exists")
    directories, names = _inventory(source)
    if not names:
        raise ValueError("cannot stage an empty model")
    sources = {name: (source / name).resolve() for name in names}
    for path in sources.values():
        _identity(path)
    destination.mkdir(mode=0o700)  # Exclusive ownership, never reuse an existing tree.
    record = {"schema": "behave.staged-model/1", "state": "copying", "source": str(source),
              "host": _transport_host(),
              "destination": str(destination), "directories": {".": _directory(destination)}, "files": {}}
    write_json(receipt, record, durable=True)
    try:
        for name in sorted(directories, key=lambda name: len(Path(name).parts)):
            if name != ".":
                (destination / name).mkdir(mode=0o700)
                record["directories"][name] = _directory(destination / name)
        for name in names:
            record["files"][name] = _copy(sources[name], destination / name)
        if any(_identity(Path(row["source"])) != row["source_identity"] for row in record["files"].values()):
            raise ValueError("retained model changed during staging")
        record["state"] = "verified"
        write_json(receipt, record, durable=True)
    except Exception as error:
        record.update(state="interrupted", error=f"{type(error).__name__}: {error}")
        write_json(receipt, record, durable=True)
        raise
    return destination


def _member(name, *, directory=False):
    path = Path(name)
    if (path.is_absolute() or ".." in path.parts or path.as_posix() != name
            or (name == "." and not directory)):
        raise ValueError("unsafe staged model inventory")
    return path


def release_model(destination, *, receipt, cleanup):
    """Release an unchanged private copy only after its owner's cleanup witness.

    A persisted releasing inventory allows retry after a partial unlink. Missing
    or modified retained sources, unexpected files, changed inodes and uncertain
    process cleanup retain the copy. No source tree or kernel cache is removed.
    """
    if type(cleanup) is not CleanupState:
        raise TypeError("staged model release requires a typed cleanup witness")
    cleanup.require_quiescent("staged-model")
    destination, receipt = Path(destination).absolute(), Path(receipt).absolute()
    _directory(destination.parent)
    if receipt.resolve() != receipt:
        raise ValueError("model staging receipt requires an unlinked path")
    _identity(receipt)
    record = json.loads(receipt.read_bytes())
    if (record["schema"] != "behave.staged-model/1" or record["destination"] != str(destination)
            or record["host"] != _transport_host()
            or record["state"] not in {"verified", "releasing", "released"}
            or not record["files"] or "." not in record["directories"]
            or receipt.is_relative_to(destination)):
        raise ValueError("unverified or mismatched staged model receipt")
    for name in record["directories"]:
        _member(name, directory=True)
    expected_directories = {"."}
    for name, row in record["files"].items():
        expected_directories.update(p.as_posix() for p in _member(name).parents)
        source = Path(row["source"])
        if (not source.is_absolute() or source.resolve() != source or source.is_relative_to(destination)
                or _identity(source) != row["source_identity"]):
            raise ValueError("retained source changed; staged model retained")
    if not expected_directories <= record["directories"].keys():
        raise ValueError("incomplete staged model directory inventory")
    if destination.exists() or destination.is_symlink():
        directories, files = _inventory(destination)
    else:
        directories, files = {}, []
    partial = record["state"] in {"releasing", "released"}
    if (set(files) - record["files"].keys() or directories.keys() - record["directories"].keys()
            or (not partial and (set(files) != record["files"].keys()
                                 or directories.keys() != record["directories"].keys()))):
        raise ValueError("staged model inventory differs; retained")
    if any(identity != record["directories"][name] for name, identity in directories.items()):
        raise ValueError("staged model directory changed; retained")
    for name in files:
        if _identity(destination / name) != record["files"][name]["local_identity"]:
            raise ValueError("staged model file changed; retained")
    if record["state"] == "released":
        if directories or files:
            raise ValueError("released staged model reappeared; retained")
        return record
    if record["state"] == "verified":
        record.update(state="releasing", cleanup=cleanup.value,
                      release_inventory={name: record["files"][name]["local_identity"] for name in files})
        write_json(receipt, record, durable=True)  # Must commit before the first unlink.
    if record.get("release_inventory") != {name: row["local_identity"] for name, row in record["files"].items()}:
        raise ValueError("staged model release inventory differs")
    for name in files:
        path = destination / name
        parent = path.parent.relative_to(destination).as_posix()
        if (_directory(path.parent) != record["directories"][parent]
                or _identity(Path(record["files"][name]["source"])) != record["files"][name]["source_identity"]
                or _identity(path) != record["release_inventory"][name]):
            raise ValueError("model changed before unlink; retained")
        path.unlink()
    for name in sorted(directories, key=lambda name: len(Path(name).parts), reverse=True):
        path = destination / name
        if _directory(path) != record["directories"][name]:
            raise ValueError("staged model directory changed before release")
        path.rmdir()  # Only empty inventoried directories, never recursive removal.
    record["state"] = "released"
    write_json(receipt, record, durable=True)
    return record


@dataclass(frozen=True, slots=True)
class CheckpointFile:
    path: str
    size_bytes: int

    def __post_init__(self):
        relative_path(self.path)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("checkpoint file size must be a nonnegative integer")

    def as_dict(self):
        return {"path": self.path, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, value):
        if "content" in value:
            # Legacy receipts remain usable. Hash checking is now offline,
            # including when the historical receipt happens to have a hash.
            file = FileRef.from_dict(value)
            return cls(file.path, file.content.size_bytes)
        row = fields(value, {"path", "size_bytes"})
        return cls(row["path"], row["size_bytes"])


def model_handoff(model, checkpoint, *, materialize=True):
    """Describe the existing HF export without reading weight tensors."""
    from behave.runtime.models import cold_model, restore_model

    receipt = cold_model(model)
    if receipt is not None and not materialize:
        # Scheduling must not rehydrate large weights onto NFS. The node will
        # verify the whole packet and model layout in its own scratch later.
        files = sorted(
            (CheckpointFile.from_dict(row).as_dict() for row in receipt["identity"]["files"]),
            key=lambda row: row["path"],
        )
        names = {row["path"] for row in files}
        if len(names) != len(files) or not {"config.json", "tokenizer.json", "tokenizer_config.json"} <= names:
            raise ValueError("invalid cold HF metadata inventory")
        if not any(name.endswith(".safetensors") for name in names):
            raise ValueError("cold HF inventory has no weight shards")
    else:
        if receipt is not None:
            restore_model(model)
        from behave.serving.model_files import inspect_model_files  # serving is outside the recovery snapshots

        inspect_model_files(model)
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            read_regular_file(model / name)
        paths = sorted(path for path in model.rglob("*") if path.is_file())
        files = [CheckpointFile(path.relative_to(model).as_posix(), path.stat().st_size).as_dict() for path in paths]
    weights = {"schema": "behave.native-hf-handoff/2", "checkpoint": checkpoint.as_dict(), "files": files}
    return {"model_path": str(model), "model_revision": BlobRef.of(canonical_json(weights)).sha256, "weights": weights}


def periodic_handoff(job: Path, updates: int, *, materialize=True) -> dict:
    """Serve a saved periodic HF export; native optimizer retention is unrelated."""
    job = job.resolve()
    native = JournalDirectory(job / "artifacts/plan/rounds/native")
    saved = next(
        event
        for event in reversed(native.journal("rounds").load(verify_artifacts=False))
        if event.kind == "checkpoint_saved" and event.payload["round"] == updates - 1
    )
    checkpoint = BlobRef.of(saved.to_bytes())
    return {
        "job": str(job),
        "step": updates - 1,
        "checkpoint": checkpoint.as_dict(),
        **model_handoff(job / f"artifacts/plan/hf_{updates - 1}", checkpoint, materialize=materialize),
    }
