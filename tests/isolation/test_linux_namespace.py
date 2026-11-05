from __future__ import annotations

import os
import stat
import sysconfig
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.isolation

from behave.isolation import (
    IsolationRefusal,
    IsolationSpec,
    IsolationStatus,
    NetworkMode,
    ResourceLimits,
    WritableRoot,
    binary,
    capabilities,
    python_runtime,
    read_only,
    run,
)
from behave.isolation._linux_child import _mounts_below


def _available():
    got = capabilities()
    if type(got) is IsolationRefusal:
        pytest.skip(f"namespace isolation unavailable: {got.code}: {got.why}")
    return got


@pytest.fixture(scope="module")
def runtime():
    _available()
    return python_runtime()


def _python_spec(
    runtime,
    code: str,
    *,
    limits: ResourceLimits | None = None,
    environment: tuple[tuple[str, str], ...] = (),
    read_only=(),
):
    binaries, mounts, masked = runtime
    limits = limits or ResourceLimits(
        cpu_seconds=5,
        rss_bytes=256 << 20,
        processes=1,
        file_bytes=1 << 20,
        stdout_bytes=1 << 20,
        stderr_bytes=1 << 20,
        protocol_bytes=1 << 20,
        wall_seconds=8,
    )
    return IsolationSpec(
        argv=(binaries[0].target, "-S", "-c", code),
        binaries=binaries,
        read_only=(*mounts, *read_only),
        writable=(WritableRoot("/work", limits.file_bytes),),
        masked=masked,
        environment=(
            ("PYTHONHASHSEED", "0"),
            ("PYTHONHOME", "/runtime/python"),
            *environment,
        ),
        limits=limits,
    )


def test_minimal_root_and_scrubbed_environment(runtime, monkeypatch):
    monkeypatch.setenv("BEHAVE_ISOLATION_SECRET", "must-not-cross")
    spec = _python_spec(
        runtime,
        "import os; print(os.path.exists('/etc/passwd')); "
        "print(os.environ.get('BEHAVE_ISOLATION_SECRET')); print(os.environ['ALLOWED'])",
        environment=(("ALLOWED", "yes"),),
    )
    result = run(spec)
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"False\nNone\nyes\n"
    assert result.backend_identity != spec.digest


def test_python_runtime_exposes_an_existing_embed_library(runtime):
    _binaries, mounts, _masked = runtime
    libdir = sysconfig.get_config_var("LIBDIR")
    names = {
        name for name in (
            sysconfig.get_config_var("LDLIBRARY"),
            sysconfig.get_config_var("INSTSONAME"),
        )
        if type(name) is str and name
    }
    existing = {
        name for name in names
        if type(libdir) is str and (Path(libdir) / name).is_file()
    }
    targets = {mount.target for mount in mounts}
    assert {
        f"/runtime/python/lib/{name}" for name in existing
    } <= targets


def test_outside_write_traversal_symlink_and_openat_cannot_escape(runtime, tmp_path):
    marker = tmp_path / "outside"
    code = f"""
import os
answers=[]
for action in (
    lambda: open({str(marker)!r}, 'w').write('escaped'),
    lambda: (os.symlink('/etc/passwd', '/work/link'), open('/work/link').read())[1],
    lambda: os.open('../../etc/passwd', os.O_RDONLY, dir_fd=os.open('/work', os.O_RDONLY)),
):
    try:
        action(); answers.append('escaped')
    except OSError:
        answers.append('denied')
open('/work/allowed', 'w').write('ok')
print(','.join(answers))
"""
    result = run(_python_spec(runtime, code))
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"denied,denied,denied\n"
    assert not marker.exists()


def test_network_fork_and_subprocess_are_denied(runtime):
    code = """
import os, socket, subprocess, sys
for operation in (
    lambda: socket.socket(),
    lambda: os.fork(),
    lambda: subprocess.run([sys.executable, '-c', 'pass']),
):
    try:
        operation(); print('escaped')
    except OSError:
        print('denied')
"""
    result = run(_python_spec(runtime, code))
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"denied\ndenied\ndenied\n"


def test_private_loopback_allows_ipc_but_not_host_or_external_network(runtime):
    import socket

    with socket.socket() as host:
        host.bind(("127.0.0.1", 0))
        host.listen()
        code = f"""
import os, socket
assert {{name for _, name in socket.if_nameindex()}} == {{'lo'}}
assert os.stat('/proc/self/ns/net').st_ino != {os.stat('/proc/self/ns/net').st_ino}
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    server.listen()
    with socket.create_connection(server.getsockname(), timeout=1) as client:
        peer, _ = server.accept()
        with peer:
            client.sendall(b'local')
            assert peer.recv(5) == b'local'
left, right = socket.socketpair()
with left, right:
    left.sendall(b'ipc')
    assert right.recv(3) == b'ipc'
print('local-ipc-passed')
"""
        code += f"""
for target in [('127.0.0.1', {host.getsockname()[1]}), ('198.51.100.1', 9)]:
    try:
        connection = socket.create_connection(target, timeout=.25)
    except OSError:
        print('unreachable')
    else:
        connection.close()
        raise AssertionError('network namespace escaped')
try:
    os.fork()
except OSError:
    print('fork-denied')
else:
    raise AssertionError('single-process policy escaped')
"""
        default = _python_spec(runtime, code)
        spec = replace(default, network=NetworkMode.PRIVATE_LOOPBACK)
        assert spec.digest != default.digest
        result = run(spec)
    assert result.status is IsolationStatus.COMPLETED
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"local-ipc-passed\nunreachable\nunreachable\nfork-denied\n"


def test_private_loopback_refuses_the_host_namespace():
    from behave.isolation._linux_child import _private_loopback

    with pytest.raises(ValueError, match="requires a new network namespace"):
        _private_loopback(os.stat("/proc/self/ns/net").st_ino)


def test_output_flood_is_bounded_before_buffering(runtime):
    limits = ResourceLimits(
        cpu_seconds=5,
        rss_bytes=256 << 20,
        processes=1,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=8,
    )
    result = run(_python_spec(runtime, "import os; os.write(1, b'x' * (1 << 20))", limits=limits))
    assert result.status is IsolationStatus.OUTPUT_LIMIT
    assert len(result.stdout) <= limits.stdout_bytes


def test_hang_and_allocation_end_only_the_isolated_tree(runtime):
    timed = ResourceLimits(
        cpu_seconds=10,
        rss_bytes=256 << 20,
        processes=1,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=1,
    )
    result = run(_python_spec(runtime, "import time; time.sleep(30)", limits=timed))
    assert result.status is IsolationStatus.TIMED_OUT

    memory = ResourceLimits(
        cpu_seconds=10,
        rss_bytes=64 << 20,
        processes=1,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=8,
    )
    result = run(_python_spec(runtime, "x=[]\nwhile True: x.append(bytearray(1 << 20))", limits=memory))
    assert result.status is IsolationStatus.MEMORY_LIMIT


def test_shared_pages_count_once_against_the_tree_memory_limit(runtime):
    """A forked shared image is one physical allocation, not two RSS copies."""

    limits = ResourceLimits(
        cpu_seconds=10,
        rss_bytes=112 << 20,
        processes=2,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=8,
    )
    code = """
import os, time
image = bytearray(64 << 20)
for offset in range(0, len(image), 4096):
    image[offset] = 1
child = os.fork()
if child == 0:
    time.sleep(0.5)
    os._exit(0)
os.waitpid(child, 0)
print('shared')
"""
    result = run(_python_spec(runtime, code, limits=limits))
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"shared\n"


def test_daemon_is_reaped_when_its_direct_parent_exits(runtime):
    limits = ResourceLimits(
        cpu_seconds=10,
        rss_bytes=256 << 20,
        processes=2,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=8,
    )
    result = run(
        _python_spec(
            runtime,
            "import os,time\nif os.fork()==0: time.sleep(30)\nelse: print('parent-done')",
            limits=limits,
        )
    )
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"parent-done\n"


def test_fast_target_exit_cannot_be_stolen_by_the_daemon_reaper(runtime):
    """The target status has one owner even when an adopted child is still alive."""

    limits = ResourceLimits(
        cpu_seconds=10,
        rss_bytes=256 << 20,
        processes=2,
        file_bytes=1 << 20,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=8,
    )
    for _attempt in range(8):
        result = run(
            _python_spec(
                runtime,
                "import os,time\nif os.fork()==0: time.sleep(30)",
                limits=limits,
            )
        )
        assert result.status is IsolationStatus.COMPLETED
        assert result.exit_code == 0


def test_recursive_read_only_mount_enumerates_nested_mounts_deepest_first():
    lines = (
        "10 1 0:1 / /sandbox rw - tmpfs tmpfs rw",
        "11 10 0:2 / /sandbox/runtime rw - ext4 disk rw",
        r"12 11 0:3 / /sandbox/runtime/a\040b rw - tmpfs tmpfs rw",
        r"13 12 0:4 / /sandbox/runtime/a\040b/deeper rw - tmpfs tmpfs rw",
        "14 1 0:5 / /unrelated rw - tmpfs tmpfs rw",
    )

    assert _mounts_below("/sandbox/runtime", lines) == (
        "/sandbox/runtime/a b/deeper",
        "/sandbox/runtime/a b",
        "/sandbox/runtime",
    )


def test_read_only_input_is_digest_bound_and_cannot_be_changed(runtime, tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("frozen")
    mounted = read_only(str(source), "/input/data")
    code = """
print(open('/input/data').read())
try:
    open('/input/data', 'w').write('changed')
    print('writable')
except OSError:
    print('read-only')
"""
    spec = _python_spec(runtime, code, read_only=(mounted,))
    result = run(spec)
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"frozen\nread-only\n"
    assert source.read_text() == "frozen"

    source.write_text("replaced")
    result = run(spec)
    assert result.status is IsolationStatus.REFUSED
    assert result.refusal.code == "isolation-input-changed"


def test_nested_file_mount_reuses_a_read_only_parent_mountpoint(runtime, tmp_path):
    """A specific library may refine a directory already mounted read-only.

    The EDA runtime exposes ``/usr/lib/...`` as one trusted tree and the backend adds
    its exact libseccomp file below it.  The child used ``Path.touch(exist_ok=True)`` on
    that already-existing file, which attempts a metadata write through the read-only
    parent and fails with EACCES before seccomp can load.
    """
    broad = tmp_path / "broad"
    broad.mkdir()
    (broad / "library.so").write_text("from-parent")
    replacement = tmp_path / "library.so"
    replacement.write_text("from-leaf")

    result = run(
        _python_spec(
            runtime,
            "print(open('/runtime/lib/library.so').read())",
            read_only=(
                read_only(str(broad), "/runtime/lib"),
                read_only(str(replacement), "/runtime/lib/library.so"),
            ),
        )
    )
    assert result.status is IsolationStatus.COMPLETED, result
    assert result.stdout == b"from-leaf\n"


def test_existing_mountpoint_rejects_links_special_files_and_wrong_kinds(tmp_path):
    from behave.isolation._linux_child import _make_target

    root = tmp_path / "root"
    root.mkdir()
    source_file = tmp_path / "source-file"
    source_file.write_text("x")
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()

    (root / "link").symlink_to(source_file)
    with pytest.raises(OSError, match="symlink"):
        _make_target(str(root), "/link", str(source_file))

    os.mkfifo(root / "fifo")
    with pytest.raises(OSError, match="regular-file mountpoint"):
        _make_target(str(root), "/fifo", str(source_file))

    (root / "existing-directory").mkdir()
    with pytest.raises(OSError, match="regular-file mountpoint"):
        _make_target(str(root), "/existing-directory", str(source_file))

    (root / "existing-file").write_text("x")
    with pytest.raises(OSError, match="directory mountpoint"):
        _make_target(str(root), "/existing-file", str(source_dir))


def test_mountpoint_parents_reject_links_and_non_directories(tmp_path):
    from behave.isolation._linux_child import _make_target

    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source"
    source.write_text("x")

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked-parent").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="parent is a symlink"):
        _make_target(str(root), "/linked-parent/leaf", str(source))
    assert not (outside / "leaf").exists()

    (root / "file-parent").write_text("x")
    with pytest.raises(OSError, match="parent is not a directory"):
        _make_target(str(root), "/file-parent/leaf", str(source))


def test_mountpoint_skeleton_is_traversable_under_a_restrictive_umask(tmp_path):
    """Ambient launcher policy cannot hide trusted mounts from a later uid.

    A 0077 umask used to create ``/runtime`` and its descendants as 0700.  The
    namespace's first process could traverse them, but a tool that dropped
    credentials could not reach a mounted executable below ``/runtime``.
    """
    from behave.isolation._linux_child import _make_target

    root = tmp_path / "root"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    source = tmp_path / "python"
    source.write_text("runtime")

    previous_umask = os.umask(0o077)
    try:
        _make_target(str(root), "/runtime/python/bin/python", str(source))
    finally:
        os.umask(previous_umask)

    for relative in ("runtime", "runtime/python", "runtime/python/bin"):
        mode = os.stat(root / relative, follow_symlinks=False).st_mode
        assert stat.S_IMODE(mode) == 0o755


def test_no_parent_directory_descriptor_crosses_the_boundary(runtime, tmp_path):
    secret = tmp_path / "fd-secret"
    secret.write_text("secret")
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        code = """
import os
seen=[]
for name in os.listdir('/proc/self/fd'):
    try:
        target=os.readlink('/proc/self/fd/'+name)
    except OSError:
        continue
    seen.append(target)
print(any('fd-secret' in value for value in seen))
"""
        result = run(_python_spec(runtime, code))
    finally:
        os.close(descriptor)
    assert result.status is IsolationStatus.COMPLETED
    assert result.stdout == b"False\n"


def test_target_setup_failure_is_reported_before_the_job_becomes_ready(monkeypatch):
    """A broken seccomp/capability/exec setup is infrastructure refusal, not exit 126."""

    import json

    from behave.isolation import _linux_child as child

    def broken_target(_spec, _setup_fd):
        raise OSError("seccomp changed after the capability probe")

    monkeypatch.setattr(child, "_target_process", broken_target)
    status_r, status_w = os.pipe()
    try:
        child._supervise({}, status_w)
        os.close(status_w)
        status_w = -1
        payload = json.loads(os.read(status_r, 8192))
    finally:
        os.close(status_r)
        if status_w >= 0:
            os.close(status_w)

    assert payload["kind"] == "refused"
    assert payload["code"] == "isolation-target-setup-failed"
    assert "seccomp changed" in payload["why"]


def test_exec_setup_failure_is_a_typed_namespace_refusal(tmp_path):
    _available()
    broken = tmp_path / "broken-tool"
    broken.write_text("#!/runtime/that-does-not-exist\n")
    broken.chmod(0o755)
    limits = ResourceLimits(
        cpu_seconds=2,
        rss_bytes=64 << 20,
        processes=1,
        file_bytes=4096,
        stdout_bytes=4096,
        stderr_bytes=4096,
        protocol_bytes=4096,
        wall_seconds=4,
    )
    spec = IsolationSpec(
        argv=("/bin/broken-tool",),
        binaries=(binary(str(broken), "/bin/broken-tool"),),
        writable=(WritableRoot("/work", limits.file_bytes),),
        limits=limits,
    )

    result = run(spec)

    assert result.status is IsolationStatus.REFUSED
    assert result.refusal.code == "isolation-target-setup-failed"


def test_concurrent_failed_starts_do_not_break_successful_jobs(runtime, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    broken = tmp_path / "broken-tool"
    broken.write_text("#!/runtime/that-does-not-exist\n")
    broken.chmod(0o755)
    success = _python_spec(runtime, "print('intact')")
    failure = replace(success, argv=("/bin/broken-tool",),
                      binaries=(binary(str(broken), "/bin/broken-tool"),))
    specs = (failure, success) * 16
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = tuple(workers.map(run, specs))
    for spec, result in zip(specs, results, strict=True):
        if spec is failure:
            assert result.status is IsolationStatus.REFUSED
            assert result.refusal.code == "isolation-target-setup-failed"
            assert "cleanup" not in result.refusal.detail
        else:
            assert result.status is IsolationStatus.COMPLETED, result
            assert result.exit_code == 0
            assert result.stdout == b"intact\n"
