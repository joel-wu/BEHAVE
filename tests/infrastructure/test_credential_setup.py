"""Explicit credential setup, with synthetic values only and no provider calls."""

import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

import pytest

from behave.agent.providers import credentials
from behave.agent.providers.credentials import CredentialRef, create_credential_file, load_credential
from behave.cli.credentials import main


def reference(path):
    return CredentialRef(str(path), "FIXTURE_KEY", "fixture-deployment/1")


@pytest.mark.parametrize("value", ["fixture-secret", "fixture-'quotes\"#=", "fixture-" + "x" * 8192],
                         ids=["ordinary", "literal-punctuation", "long"])
def test_setup_reads_only_the_explicit_binding_and_reuses_the_broker_parser(tmp_path, value):
    class SelectedBinding(Mapping):
        def __getitem__(self, key):
            assert key == "FIXTURE_KEY"
            return value

        def __iter__(self):
            raise AssertionError("setup must not copy the operator environment")

        def __len__(self):
            raise AssertionError("setup needs only the named binding")

    path = tmp_path / "private.env"
    ref = reference(path)
    assert create_credential_file(ref, environment=SelectedBinding()) is ref
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_uid == os.getuid()
    assert path.read_text() == f'FIXTURE_KEY="{value}"\n'
    assert load_credential(ref).authorization("fireworks") == {"Authorization": "Bearer " + value}
    assert value not in repr(ref) and value not in repr(load_credential(ref))
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("value", [None, 7, "", "has space", "line\nbreak", "tab\tvalue", "null\0value",
                                  "non-ascii-密钥", "$variable", "back`tick", "back\\slash"])
def test_invalid_environment_binding_does_not_publish_a_file(tmp_path, value):
    with pytest.raises(ValueError):
        create_credential_file(reference(tmp_path / "private.env"), environment={"FIXTURE_KEY": value})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("existing", ["same-file", "other-file", "symlink", "dangling-symlink", "directory"])
def test_setup_never_replaces_an_existing_destination(tmp_path, existing):
    path, target = tmp_path / "private.env", tmp_path / "target.env"
    if existing == "directory":
        path.mkdir()
    elif "symlink" in existing:
        if existing == "symlink":
            target.write_text("untouched-target")
        path.symlink_to(target)
    else:
        path.write_text('FIXTURE_KEY="fixture-new"\n' if existing == "same-file" else "untouched-old")
    before = path.lstat()
    content = path.read_bytes() if path.is_file() else None
    with pytest.raises(FileExistsError):
        create_credential_file(reference(path), environment={"FIXTURE_KEY": "fixture-new"})
    assert path.lstat() == before
    if content is not None:
        assert path.read_bytes() == content
    if existing == "dangling-symlink":
        assert not target.exists()
    assert not list(tmp_path.glob(".behave-credential-*"))


def test_setup_requires_an_existing_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_credential_file(reference(tmp_path / "missing/private.env"), environment={"FIXTURE_KEY": "fixture"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stage", ["write", "file-sync", "validate", "link", "directory-open", "directory-sync"])
def test_publication_failures_leave_no_partial_destination(tmp_path, monkeypatch, stage):
    path = tmp_path / "private.env"
    ref = reference(path)
    original_sync, original_open = os.fsync, os.open
    original_temporary = tempfile.NamedTemporaryFile
    syncs = 0

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "fixture failure")

    def sync(fd):
        nonlocal syncs
        syncs += 1
        if syncs == (1 if stage == "file-sync" else 2):
            fail()
        return original_sync(fd)

    def open_directory(name, flags, *args, **kwargs):
        if flags & os.O_DIRECTORY:
            fail()
        return original_open(name, flags, *args, **kwargs)

    @contextmanager
    def interrupted_write(*args, **kwargs):
        with original_temporary(*args, **kwargs) as temporary:
            original_write = temporary.write

            def write_prefix(value):
                original_write(value[:5])
                temporary.flush()
                fail()

            temporary.write = write_prefix
            yield temporary

    with monkeypatch.context() as fault:
        if stage == "write":
            fault.setattr(tempfile, "NamedTemporaryFile", interrupted_write)
        elif stage.endswith("sync"):
            fault.setattr(os, "fsync", sync)
        elif stage == "directory-open":
            fault.setattr(os, "open", open_directory)
        else:
            fault.setattr(credentials if stage == "validate" else os,
                          "load_credential" if stage == "validate" else "link", fail)
        with pytest.raises(OSError):
            create_credential_file(ref, environment={"FIXTURE_KEY": "fixture-complete"})
    # The link is the visibility boundary. A later durability failure cannot
    # safely erase a complete file that another consumer may already have read.
    if stage.startswith("directory-"):
        assert load_credential(ref).authorization("openai") == {"Authorization": "Bearer fixture-complete"}
        with pytest.raises(FileExistsError):
            create_credential_file(ref, environment={"FIXTURE_KEY": "fixture-replacement"})
        assert list(tmp_path.iterdir()) == [path]
    else:
        assert not list(tmp_path.iterdir())


def test_concurrent_setup_publishes_exactly_one_complete_private_file(tmp_path, monkeypatch):
    ref = reference(tmp_path / "private.env")
    ready, original_link = Barrier(2), os.link

    def link(*args, **kwargs):
        ready.wait(timeout=10)
        return original_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", link)

    def create(value):
        try:
            create_credential_file(ref, environment={"FIXTURE_KEY": value})
        except FileExistsError:
            return None
        return value

    with ThreadPoolExecutor(max_workers=2) as workers:
        winners = [value for value in workers.map(create, ["fixture-one", "fixture-two"]) if value is not None]
    assert len(winners) == 1
    assert load_credential(ref).authorization("openai") == {"Authorization": "Bearer " + winners[0]}
    assert stat.S_IMODE(Path(ref.path).stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [Path(ref.path)]


@pytest.mark.parametrize("failure,code", [
    (OSError(errno.EIO, "fixture-secret", "fixture-private-path"), "credential-write-failed"),
    (OSError("fixture-secret"), "credential-write-failed"),
    (ValueError("fixture-secret"), "credential-input-invalid"),
    (TypeError("fixture-secret"), "credential-input-invalid"),
    (FileExistsError("fixture-secret"), "credential-destination-exists"),
])
def test_cli_errors_never_print_private_exception_contents(tmp_path, monkeypatch, capsys, failure, code):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(credentials, "create_credential_file", fail)
    assert main(["--from-env", "FIXTURE_KEY", "--output", str(tmp_path / "private.env"),
                 "--revision", "fixture/1"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "fixture-secret" not in captured.out and "fixture-private-path" not in captured.out
    assert json.loads(captured.out)["code"] == code
    assert not list(tmp_path.iterdir())


def test_cli_does_not_resolve_a_relative_dangling_destination(tmp_path, monkeypatch, capsys):
    path, target = tmp_path / "private.env", tmp_path / "untouched.env"
    path.symlink_to(target)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FIXTURE_KEY", "fixture-cli-value")
    assert main(["--from-env", "FIXTURE_KEY", "--output", "private.env", "--revision", "fixture/1"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and "fixture-cli-value" not in captured.out
    assert json.loads(captured.out)["code"] == "credential-destination-exists"
    assert path.is_symlink() and not target.exists()
    assert list(tmp_path.iterdir()) == [path]


# Also executed against the installed wheel, outside the source checkout.
CREDENTIAL_SETUP_PROBE = '''
import io
import json
import os
import stat
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from behave.cli.__main__ import main
from behave.agent.providers.credentials import CredentialRef, load_credential

root = Path(sys.argv[1])
os.chdir(root)
value = 'fixture-installed-private-value'
arguments = ['credentials', '--from-env', 'FIXTURE_KEY', '--output', 'private.env', '--revision', 'fixture/1']
def invoke():
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(arguments)
    assert value not in output.getvalue()
    return status, json.loads(output.getvalue())

with patch.dict(os.environ, {'FIXTURE_KEY': value, 'UNRELATED_KEY': 'fixture-unselected'}, clear=True), \\
     patch('socket.socket.connect', side_effect=AssertionError('no network during setup')), \\
     patch('subprocess.Popen', side_effect=AssertionError('no worker during setup')):
    old_mask = os.umask(0)
    try:
        status, row = invoke()
    finally:
        os.umask(old_mask)
    assert status == 0
    assert row == {'path': str(root / 'private.env'), 'key': 'FIXTURE_KEY', 'revision': 'fixture/1'}
    ref = CredentialRef.from_dict(row)
    path = Path(ref.path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == f'FIXTURE_KEY="{value}"\\n'
    assert load_credential(ref).authorization('fireworks') == {'Authorization': 'Bearer ' + value}
    before = path.stat()
    status, row = invoke()
    assert status == 1 and row['code'] == 'credential-destination-exists'
    assert path.stat() == before
    path.rename(root / 'retained.env')
    os.environ.pop('FIXTURE_KEY')
    status, row = invoke()
    assert status == 1 and row['code'] == 'credential-input-invalid'
assert list(root.iterdir()) == [root / 'retained.env']
assert not any(name == 'tools' or name.startswith('tools.') for name in sys.modules)
'''


def test_cli_routes_offline_with_private_permissions_and_only_public_output(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    bootstrap = f"import sys; sys.path.insert(0, {str(source)!r})\n"
    process = subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap + CREDENTIAL_SETUP_PROBE, str(tmp_path)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert process.returncode == 0, process.stderr
    assert process.stdout == process.stderr == ""
