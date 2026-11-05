"""PID-1 setup and supervisor for the Linux namespace backend.

This file is executed by a pristine host interpreter before the guest root exists.  It
is intentionally standalone: importing the package here would make the package path an
ambient dependency of the boundary it is constructing.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import signal
import stat
import time
from pathlib import Path

MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18
PR_SET_NO_NEW_PRIVS = 38
PR_CAPBSET_DROP = 24
LINUX_CAPABILITY_VERSION_3 = 0x20080522
SECCOMP_ACT_ALLOW = 0x7FFF0000
SECCOMP_ACT_ERRNO = 0x00050000


libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
libc.mount.restype = ctypes.c_int
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.umount2.restype = ctypes.c_int
libc.prctl.restype = ctypes.c_int


def _bytes(value: str | None) -> bytes | None:
    return None if value is None else os.fsencode(value)


def _mount(source: str | None, target: str, kind: str | None, flags: int, data: str | None = None) -> None:
    if libc.mount(_bytes(source), _bytes(target), _bytes(kind), flags, _bytes(data)):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


_MOUNTINFO_ESCAPES = {
    r"\040": " ",
    r"\011": "\t",
    r"\012": "\n",
    r"\134": "\\",
}


def _mountinfo_path(value: str) -> str:
    for encoded, decoded in _MOUNTINFO_ESCAPES.items():
        value = value.replace(encoded, decoded)
    return value


def _mounts_below(target: str, lines: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Mountpoints in ``target``'s recursive bind, deepest first.

    A recursive bind followed by a plain bind-remount changes only the top mount.  A
    nested host mount would otherwise retain its writable flag inside the guest.  Parse
    the process's mount namespace rather than assuming trusted trees contain no mounts.
    """

    if lines is None:
        lines = tuple(Path("/proc/self/mountinfo").read_text().splitlines())
    target = os.path.normpath(target)
    prefix = target + os.sep
    found = []
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            raise ValueError("a /proc/self/mountinfo row is malformed")
        mountpoint = os.path.normpath(_mountinfo_path(fields[4]))
        if mountpoint == target or mountpoint.startswith(prefix):
            found.append(mountpoint)
    return tuple(sorted(set(found), key=lambda path: (path.count(os.sep), path), reverse=True))


def _remount_readonly_tree(target: str, flags: int) -> None:
    mountpoints = _mounts_below(target)
    if target not in mountpoints:
        raise RuntimeError("the recursive bind is absent from the mount table")
    for mountpoint in mountpoints:
        _mount(None, mountpoint, None, MS_BIND | MS_REMOUNT | MS_RDONLY | flags)


def _target(root: str, guest: str) -> str:
    return os.path.join(root, guest.lstrip("/"))


def _directory(path: str) -> None:
    """Create one mountpoint parent without following an existing link.

    Mount targets are resolved before ``chroot``.  Following a link from a previously
    mounted tree here could therefore select a host path outside ``root``.  Existing
    components must be real directories; missing components are created one at a time.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        # These directories are the guest's mountpoint skeleton, not private
        # storage.  Every later executable and read-only mount may sit below one,
        # so traversal must remain possible after a target or one of its tools
        # drops credentials.  ``mkdir``'s requested mode is filtered by the
        # launcher's ambient umask; make the invariant explicit on the inode we
        # just created instead of inheriting that ambient policy.
        os.mkdir(path, 0o755)
        descriptor_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            descriptor_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            descriptor_flags |= os.O_NOFOLLOW
        descriptor = os.open(path, descriptor_flags)
        try:
            os.fchmod(descriptor, 0o755)
        finally:
            os.close(descriptor)
        return
    if stat.S_ISLNK(mode):
        raise OSError(errno.ELOOP, "a mountpoint parent is a symlink", path)
    if not stat.S_ISDIR(mode):
        raise OSError(errno.ENOTDIR, "a mountpoint parent is not a directory", path)


def _make_target(root: str, guest: str, source: str | None = None) -> str:
    target = _target(root, guest)
    root_path = Path(root)
    _directory(str(root_path))
    current = root_path
    for part in Path(guest.lstrip("/")).parts[:-1]:
        current /= part
        _directory(str(current))

    wants_directory = source is None or stat.S_ISDIR(os.stat(source, follow_symlinks=False).st_mode)
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        if wants_directory:
            os.mkdir(target)
        else:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o600)
            os.close(descriptor)
        return target

    # A broader read-only mount may already provide this mountpoint.  Merely opening it
    # through ``Path.touch(exist_ok=True)`` performs an utime/write operation and fails
    # with EACCES/EROFS; it also mutates a writable parent for no reason.  Validate and
    # reuse the existing inode without touching it.
    if stat.S_ISLNK(mode):
        raise OSError(errno.ELOOP, "an existing mountpoint is a symlink", target)
    if wants_directory:
        if not stat.S_ISDIR(mode):
            raise OSError(errno.ENOTDIR, "a directory source needs a directory mountpoint", target)
    elif not stat.S_ISREG(mode):
        raise OSError(errno.EINVAL, "a file or device source needs a regular-file mountpoint", target)
    return target


def _bind(root: str, source: str, guest: str, *, readonly: bool, device: bool = False) -> None:
    target = _make_target(root, guest, source)
    directory = os.path.isdir(source)
    _mount(source, target, None, MS_BIND | (MS_REC if directory else 0))
    flags = MS_BIND | MS_REMOUNT | MS_NOSUID
    if not device:
        flags |= MS_NODEV
    if readonly and directory:
        _remount_readonly_tree(target, flags & ~(MS_BIND | MS_REMOUNT))
    else:
        if readonly:
            flags |= MS_RDONLY
        _mount(None, target, None, flags)


def _tmpfs(root: str, guest: str, *, size: int, readonly: bool) -> None:
    target = _make_target(root, guest)
    flags = MS_NOSUID | MS_NODEV | MS_NOEXEC
    _mount("tmpfs", target, "tmpfs", flags, f"size={size},mode=0700")
    if readonly:
        _mount(None, target, None, MS_REMOUNT | flags | MS_RDONLY)


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


def _drop_privileges() -> None:
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
        error = ctypes.get_errno()
        raise OSError(error, "could not set no-new-privileges")
    # Drop the bounding set while CAP_SETPCAP is still effective.  A missing
    # capability reports EINVAL; that marks the end of the kernel's range.
    for capability in range(64):
        if libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0):
            error = ctypes.get_errno()
            if error != errno.EINVAL:
                raise OSError(error, "could not clear the capability bounding set")
    header = _CapHeader(LINUX_CAPABILITY_VERSION_3, 0)
    data = (_CapData * 2)()
    capset = getattr(libc, "capset", None)
    if capset is None or capset(ctypes.byref(header), ctypes.byref(data)):
        error = ctypes.get_errno()
        raise OSError(error, "could not clear process capabilities")


def _seccomp(processes: int, library: str, network: str = "denied") -> None:
    """Deny network creation and, for a one-process policy, process creation.

    The private network namespace is always the confidentiality boundary. The default
    mode additionally denies every socket; private-loopback permits local IPC only.
    For policies that permit children, PID-1 supervises the exact tree count instead.
    """
    if network not in ("denied", "private-loopback"):
        raise ValueError("unsupported isolated network mode")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(SECCOMP_ACT_ALLOW)
    if not context:
        raise OSError("seccomp_init failed")
    denied = [
        "socket",
        "socketpair",
        "connect",
        "bind",
        "listen",
        "accept",
        "accept4",
        "sendto",
        "sendmsg",
        "recvfrom",
        "recvmsg",
    ] if network == "denied" else []
    if processes == 1:
        denied += ["clone", "clone3", "fork", "vfork"]
    action = SECCOMP_ACT_ERRNO | errno.EPERM
    try:
        for name in denied:
            number = seccomp.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:
                continue
            result = seccomp.seccomp_rule_add(context, action, number, 0)
            if result:
                raise OSError(-result, f"could not add seccomp rule for {name}")
        result = seccomp.seccomp_load(context)
        if result:
            raise OSError(-result, "could not load seccomp policy")
    finally:
        seccomp.seccomp_release(context)


def _close_fds(keep: set[int]) -> None:
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
        except ValueError:
            continue
        if fd > 2 and fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass


def _limits(spec: dict[str, object]) -> None:
    limits = spec["limits"]
    cpu = int(limits["cpu_seconds"])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(limits["file_bytes"]), int(limits["file_bytes"])))
    resource.setrlimit(resource.RLIMIT_NOFILE, (int(limits["open_files"]), int(limits["open_files"])))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _pids() -> list[int]:
    return sorted(int(name) for name in os.listdir("/proc") if name.isdigit() and int(name) != 1)


def _stat_cpu_ticks(line: str) -> int:
    """CPU ticks in one proc stat row, including descendants it already reaped."""

    # ``comm`` is parenthesised and may itself contain spaces.  Fields after the final
    # ``) `` begin at proc field 3; 14--17 are utime, stime, cutime and cstime.
    try:
        fields = line.rsplit(") ", 1)[1].split()
        return sum(int(fields[index]) for index in range(11, 15))
    except (IndexError, ValueError):
        raise ValueError("a /proc process stat row is malformed") from None


def _usage(pids: list[int]) -> tuple[int, float]:
    rss = 0
    ticks = 0
    for pid in pids:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss += int(line.split()[1]) * 1024
                    break
            ticks += _stat_cpu_ticks(Path(f"/proc/{pid}/stat").read_text())
        except (FileNotFoundError, ProcessLookupError):
            continue
    reaped = resource.getrusage(resource.RUSAGE_CHILDREN)
    seconds = ticks / os.sysconf("SC_CLK_TCK") \
        + reaped.ru_utime + reaped.ru_stime
    return rss, seconds


def _proportional_rss(pids: list[int]) -> int | None:
    """Physical resident footprint without counting shared pages twice.

    RSS is a cheap upper bound for the polling loop.  When it crosses the
    policy ceiling, Linux's aggregate PSS is the confirming measurement: a
    fork/vfork child temporarily maps the parent's pages, but does not allocate
    a second physical copy of them.  An unreadable live image fails closed.
    """

    total = 0
    for pid in pids:
        path = Path(f"/proc/{pid}/smaps_rollup")
        try:
            found = None
            for line in path.read_text().splitlines():
                if line.startswith("Pss:"):
                    found = int(line.split()[1]) * 1024
                    break
            if found is None:
                if _memory_released(pid):
                    continue
                return None
            total += found
        except (PermissionError, OSError, ValueError):
            if _memory_released(pid):
                continue
            return None
    return total


def _memory_released(pid: int) -> bool:
    """An exited task may retain /proc entries after freeing its address space."""

    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return fields[0] in {"Z", "X"}
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (OSError, IndexError):
        return False


def _limit_reason(
    pids: list[int], rss: int, cpu: float, elapsed: float, limits: dict[str, object],
) -> str | None:
    """Confirm the memory upper bound without suppressing the other limits."""

    if len(pids) > int(limits["processes"]):
        return "process-limit"
    if rss > int(limits["rss_bytes"]):
        proportional = _proportional_rss(pids)
        if proportional is None or proportional > int(limits["rss_bytes"]):
            return "memory-limit"
    if cpu > int(limits["cpu_seconds"]):
        return "cpu-limit"
    if elapsed > int(limits["wall_seconds"]):
        return "timed-out"
    return None


def _kill_tree() -> None:
    for pid in reversed(_pids()):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _reap(*, excluding: frozenset[int] = frozenset()) -> None:
    """Reap adopted descendants without stealing an authoritative exit status.

    PID 1 separately waits for the target process because its exact wait status is part
    of the parent protocol.  A previous ``waitpid(-1)`` could race that wait: the target
    exited after its nonblocking check, this helper reaped it and discarded the status,
    and the next target wait raised ``ChildProcessError``.  Enumerating the namespace and
    excluding the target keeps daemon cleanup independent of result ownership.
    """

    for pid in _pids():
        if pid in excluding:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            continue


def _terminate_descendants(*, timeout_s: float, poll_seconds: float) -> bool:
    """Kill and reap every process except this namespace's PID 1.

    Returning a Boolean keeps the cleanup invariant independently testable.  The
    supervisor may publish a target result only when this function returns true;
    otherwise the parent receives a typed infrastructure refusal.
    """

    deadline = time.monotonic() + timeout_s
    _kill_tree()
    while True:
        _reap()
        if not _pids():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_seconds, remaining))


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("the namespace pipe stopped accepting bytes")
        view = view[written:]


def _write_status(fd: int, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    _write_all(fd, encoded)


def _private_loopback(host_namespace: int) -> None:
    """Enable only lo, before dropping capabilities in the new network namespace."""
    import fcntl
    import socket
    import struct

    if os.stat("/proc/self/ns/net").st_ino == host_namespace:
        raise ValueError("private-loopback requires a new network namespace")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise ValueError("private-loopback namespace contains a non-loopback interface")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        current = fcntl.ioctl(control.fileno(), 0x8913, struct.pack("16sH", b"lo", 0))
        flags = struct.unpack_from("H", current, 16)[0]
        fcntl.ioctl(control.fileno(), 0x8914, struct.pack("16sH", b"lo", flags | 1))


def _setup(spec: dict[str, object]) -> None:
    network = spec.get("network", "denied")
    if network == "private-loopback":
        _private_loopback(int(spec["host_network_namespace"]))
    elif network != "denied":
        raise ValueError("unsupported isolated network mode")
    root = spec["root"]
    _mount(None, "/", None, MS_REC | MS_PRIVATE)
    Path(root).mkdir(parents=True, exist_ok=True)
    # Only trusted mountpoints live here, and the mount becomes read-only
    # before exec. Writable payload storage has its own declared quota below.
    _mount("tmpfs", root, "tmpfs", MS_NOSUID | MS_NODEV, "mode=0755")
    # Parent writable mounts must precede read-only overlays such as spec.md.
    for item in spec["writable"]:
        if item.get("source") is not None:
            _bind(root, item["source"], item["target"], readonly=False)
    for item in [*spec["binaries"], *spec["read_only"]]:
        _bind(root, item["source"], item["target"], readonly=True)
    for item in spec["devices"]:
        _bind(root, item["source"], item["target"], readonly=not item["writable"], device=True)
    for item in spec["masked"]:
        _tmpfs(root, item["target"], size=4096, readonly=True)
    for item in spec["writable"]:
        if item.get("source") is None:
            _tmpfs(root, item["target"], size=int(item["bytes"]), readonly=False)
    _make_target(root, "/proc")
    _mount("proc", _target(root, "/proc"), "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "hidepid=2")
    # The root mount itself contains only mountpoints and becomes immutable.
    _mount(None, root, None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV)
    os.chroot(root)
    os.chdir(spec["cwd"])


def _target_process(spec: dict[str, object], setup_fd: int) -> None:
    _limits(spec)
    _drop_privileges()
    _seccomp(int(spec["limits"]["processes"]), spec["seccomp_library"],
             spec.get("network", "denied"))
    # ``setup_fd`` has close-on-exec.  Keeping it until ``execve`` gives PID 1 an
    # unambiguous handshake: EOF means that the target image was entered, whereas a
    # diagnostic means that rlimit/capability/seccomp/exec setup failed.  It must
    # therefore survive our ambient-descriptor sweep but never enter the target image.
    _close_fds({setup_fd})
    environment = {name: value for name, value in spec["environment"]}
    os.write(setup_fd, b"EXEC\n")
    os.execve(spec["argv"][0], spec["argv"], environment)


def _supervise(spec: dict[str, object], status_fd: int) -> None:
    setup_r, setup_w = os.pipe2(os.O_CLOEXEC)
    child = os.fork()
    if child == 0:
        os.close(setup_r)
        try:
            _target_process(spec, setup_w)
        except BaseException as error:  # noqa: BLE001 -- PID 1 must report every target failure
            diagnostic = f"{type(error).__name__}: {error}".encode("utf-8", errors="replace")
            try:
                _write_all(setup_w, diagnostic)
            except OSError:
                pass
            # The status channel owns this diagnostic. Duplicating it on stderr
            # can fill that pipe before the parent has accepted the handshake.
            os._exit(126)
    os.close(setup_w)
    setup_reply = bytearray()
    while True:
        # Buffer size, not a quota: exec closes this descriptor, and setup
        # failures retain the entire diagnostic through EOF.
        chunk = os.read(setup_r, 65536)
        if not chunk:
            break
        setup_reply += chunk
    os.close(setup_r)
    if setup_reply != b"EXEC\n":
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
        setup_reply = setup_reply.removeprefix(b"EXEC\n")
        why = setup_reply.decode("utf-8", errors="replace") \
            or "the target setup pipe closed before exec"
        _write_status(
            status_fd,
            {
                "kind": "refused",
                "code": "isolation-target-setup-failed",
                "why": why,
            },
        )
        return
    _write_status(status_fd, {"kind": "ready", "pid": child})
    started = time.monotonic()
    limits = spec["limits"]
    reason = None
    target_status = None
    while target_status is None:
        waited, status = os.waitpid(child, os.WNOHANG)
        if waited == child:
            target_status = status
            break
        pids = _pids()
        rss, cpu = _usage(pids)
        reason = _limit_reason(pids, rss, cpu, time.monotonic() - started, limits)
        if reason is not None:
            _kill_tree()
        _reap(excluding=frozenset({child}))
        if reason is not None:
            try:
                _waited, target_status = os.waitpid(child, 0)
            except ChildProcessError:
                raise RuntimeError(
                    "the isolation supervisor lost the target's exit status") from None
            break
        time.sleep(spec["poll_seconds"])
    if reason is not None:
        payload = {"kind": "finished", "status": reason}
    elif os.WIFEXITED(target_status):
        code = os.WEXITSTATUS(target_status)
        payload = {"kind": "finished", "status": "completed" if code == 0 else "exited", "exit_code": code}
    else:
        payload = {"kind": "finished", "status": "signaled", "signal": os.WTERMSIG(target_status)}
    if not _terminate_descendants(timeout_s=spec["cleanup_seconds"], poll_seconds=spec["poll_seconds"]):
        why = "the namespace process tree did not become empty after termination"
        _write_status(
            status_fd,
            {
                "kind": "refused",
                "code": "isolation-descendant-reap-failed",
                "why": why,
                "execution": payload,
                "cleanup": {"state": "unconfirmed", "why": why},
            },
        )
        return
    _write_status(status_fd, payload)


def main() -> None:
    spec_fd = int(os.environ.pop("BEHAVE_ISOLATION_SPEC_FD"))
    status_fd = int(os.environ.pop("BEHAVE_ISOLATION_STATUS_FD"))
    startup_fd = os.environ.pop("BEHAVE_ISOLATION_STARTUP_FD", None)
    if startup_fd is not None:
        # The outer unshare process retains admission until the host receives
        # ready. No candidate may inherit the coordination descriptor.
        os.close(int(startup_fd))
    try:
        payload = b""
        while True:
            chunk = os.read(spec_fd, 65536)
            if not chunk:
                break
            payload += chunk
        os.close(spec_fd)
        spec = json.loads(payload)
        _setup(spec)
        _supervise(spec, status_fd)
    except BaseException as error:
        try:
            _write_status(
                status_fd,
                {
                    "kind": "refused",
                    "code": "isolation-setup-failed",
                    "why": f"{type(error).__name__}: {error}",
                },
            )
        except BaseException:  # noqa: BLE001,S110 -- the original setup error is authoritative
            pass
        raise
    finally:
        try:
            os.close(status_fd)
        except OSError:
            pass


if __name__ == "__main__":
    main()
