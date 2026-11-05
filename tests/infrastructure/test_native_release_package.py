"""Native input freezing uses one package implementation; no model or HDL run."""

import json
import os
from types import SimpleNamespace

import pytest

from behave.cli.native_release import main
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.data import native_release
from behave.data.published import ReleaseManifest, load_published_task, task_files

SPEC = "# Counter\n\n## Interface\n\n### Behavior interface\n\n```python\nclass Counter: ...\n```\n"


def task_fixture(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    task = root / "data/problems/fixture/counter"
    task.mkdir(parents=True)
    files = {"spec.md": SPEC.encode(), "behavior.py": b"raise AssertionError('must not execute')\n",
             "fp_lib.py": b"PRIVATE_HELPER = 1\n", "interface.json": b'{"fixture":true}',
             "task.toml": b"fixture = true\n", "rtl.sv": b"PRIVATE_AUXILIARY_RTL\n",
             "behavior2.py": b"raise AssertionError('PRIVATE_ALTERNATE_MUST_NOT_EXECUTE')\n",
             "LICENSE": b"PRIVATE_FIXTURE_LICENSE_NOT_A_RELEASE_APPROVAL\n",
             "NOTICE": b"PRIVATE_FIXTURE_NOTICE\n", "source.json": b'{"fixture":"PRIVATE_SOURCE"}'}
    for name, raw in files.items():
        (task / name).write_bytes(raw)
    task_list = tmp_path / "tasks.txt"
    task_list.write_text("counter\n")
    staged = []

    def contract(directory):
        staged.append(directory)
        assert directory != task
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == {
            name: files[name] for name in native_release.ELABORATION_FILES if name in files}
        return SimpleNamespace(as_dict=lambda: {"fixture": "contract"})

    monkeypatch.setattr(native_release.api, "contract_of_task", contract)
    return root, task, task_list, files, staged


def test_freeze_retains_inputs_and_public_private_separation(tmp_path, monkeypatch):
    root, _, tasks, files, staged = task_fixture(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    result = native_release.prepare(root, output, [tasks], workers=1)
    assert len(staged) == 1
    reader = DirectoryStore(output / "objects")
    loaded = load_published_task(reader, BlobRef.from_dict(result["release"]), "counter")
    assert {f.reference.path for f in loaded.public.files} == {"spec.md", "contract.json"}
    assert loaded.judge.reference_behavior.artifact.entry_symbol == "Counter"
    assert {f.reference.path: f.data for f in loaded.judge.reference_behavior.files} == {
        name: files[name] for name in ("behavior.py", "fp_lib.py")}
    assert loaded.judge.reference_rtl is None, "unreviewed auxiliary RTL is not a judging reference"
    assert {f.path: reader.read(f.content) for f in loaded.manifest.archive_files} == files
    inventory = json.loads((output / "inventory.json").read_bytes())
    assert {name: reader.read(BlobRef.from_dict(ref)) for name, ref in inventory[0]["input_files"].items()} == files
    policy = parse_json(loaded.judge.judge_policy)
    assert policy["search_seconds"] == 0 and policy["analysis"] is None
    release_bytes = (output / "release.json").read_bytes()
    with pytest.raises(FileExistsError):
        native_release.prepare(root, output, [tasks], workers=1)
    assert (output / "release.json").read_bytes() == release_bytes


def test_archive_captures_inputs_before_elaboration_and_excludes_unrelated_files(tmp_path, monkeypatch):
    root, task, tasks, files, _ = task_fixture(tmp_path, monkeypatch)
    (task / ".env").write_bytes(b"UNRELATED_PRIVATE_CONFIGURATION")
    (task / "compiler.tmp").write_bytes(b"UNRELATED_BUILD_OUTPUT")
    original = native_release.api.contract_of_task

    def elaborate(directory):
        contract = original(directory)
        (directory / "interface.json").write_text('{"new":"elaborated-interface"}')
        (directory / "generated.txt").write_text("not an original input")
        return contract

    monkeypatch.setattr(native_release.api, "contract_of_task", elaborate)
    output = tmp_path / "prepared"
    result = native_release.prepare(root, output, [tasks], workers=1)
    reader = DirectoryStore(output / "objects")
    task_view = load_published_task(reader, BlobRef.from_dict(result["release"]), "counter")
    archived = {f.path: reader.read(f.content) for f in task_view.manifest.archive_files}
    assert archived == files
    assert (task / "interface.json").read_bytes() == files["interface.json"]
    assert not {".env", "compiler.tmp", "generated.txt"} & archived.keys()


def test_absent_optional_attachments_are_not_invented(tmp_path, monkeypatch):
    root, task, tasks, files, _ = task_fixture(tmp_path, monkeypatch)
    for name in ("rtl.sv", "behavior2.py", "LICENSE", "NOTICE", "source.json"):
        (task / name).unlink()
        del files[name]
    output = tmp_path / "prepared"
    result = native_release.prepare(root, output, [tasks], workers=1)
    loaded = load_published_task(DirectoryStore(output / "objects"), BlobRef.from_dict(result["release"]), "counter")
    assert {f.path for f in loaded.manifest.archive_files} == files.keys()
    assert not {"license", "licence", "approval"} & loaded.manifest.metadata.keys()


@pytest.mark.parametrize("kind", ["fifo", "directory", "broken-link"])
def test_invalid_optional_attachment_is_not_silently_omitted(tmp_path, monkeypatch, kind):
    import os

    root, task, tasks, _, staged = task_fixture(tmp_path, monkeypatch)
    (task / "LICENSE").unlink()
    if kind == "fifo":
        os.mkfifo(task / "LICENSE")
    elif kind == "directory":
        (task / "LICENSE").mkdir()
    else:
        (task / "LICENSE").symlink_to(task / "missing-license")
    output = tmp_path / "refused"
    with pytest.raises((ValueError, OSError)):
        native_release.prepare(root, output, [tasks], workers=1)
    assert not staged and not (output / "release.json").exists()


def test_freeze_validates_batch_with_one_release_inventory(tmp_path, monkeypatch):
    root, _, tasks, files, staged = task_fixture(tmp_path, monkeypatch)
    second = root / "data/problems/fixture/second"
    second.mkdir()
    for name, raw in files.items():
        (second / name).write_bytes(raw)
    tasks.write_text("counter\nsecond\n")
    parses = []
    original = ReleaseManifest.from_bytes

    def parse(raw):
        parses.append(raw)
        return original(raw)

    monkeypatch.setattr(ReleaseManifest, "from_bytes", parse)
    result = native_release.prepare(root, tmp_path / "prepared", [tasks], workers=1)
    assert len(staged) == 2 and len(parses) == 1
    release = original(parses[0])
    assert {revision.task_id for revision in release.tasks} == {"counter", "second"}
    assert BlobRef.of(parses[0]) == BlobRef.from_dict(result["release"])


def test_refused_input_keeps_diagnostics_without_publishing_a_shorter_release(tmp_path, monkeypatch):
    root, _, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    bad = root / "data/problems/fixture/bad"
    bad.mkdir()
    (bad / "spec.md").write_text("# No Behavior entry\n")
    (bad / "behavior.py").write_text("raise AssertionError('must not execute')\n")
    tasks.write_text("counter\nbad\n")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="no release published"):
        native_release.prepare(root, output, [tasks], workers=2)
    assert not (output / "release.json").exists()
    inventory = json.loads((output / "inventory.json").read_bytes())
    assert {row["task_id"]: row["status"] for row in inventory} == {
        "counter": "prepared", "bad": "input_refused"}
    assert (output / "inputs/fixture/bad/spec.md").read_bytes() == (bad / "spec.md").read_bytes()


def test_installed_command_does_not_infer_a_checkout(tmp_path):
    output = tmp_path / "prepared"
    with pytest.raises(SystemExit) as error:
        main([str(output), "--tasks", str(tmp_path / "tasks.txt")])
    assert error.value.code == 2 and not output.exists()


def test_command_reports_missing_selection_before_creating_output(tmp_path, monkeypatch, capsys):
    root, _, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    tasks.write_text("absent\n")
    output = tmp_path / "prepared"
    assert main([str(output), "--repository", str(root), "--tasks", str(tasks)]) == 1
    error = parse_json(capsys.readouterr().out.encode())
    assert error["status"] == "unavailable" and "absent" in error["message"]
    assert not output.exists()


def test_data_router_delegates_to_native_inputs_command(tmp_path, monkeypatch):
    from behave.cli import native_release as command
    from behave.data.__main__ import main as data_main

    seen = []
    monkeypatch.setattr(command, "main", lambda argv: seen.append(argv) or 7)
    assert data_main(["freeze-native", str(tmp_path)]) == 7
    assert seen == [[str(tmp_path)]]


def test_selected_task_id_must_resolve_unambiguously_before_output(tmp_path, monkeypatch):
    root, _, tasks, _, staged = task_fixture(tmp_path, monkeypatch)
    duplicate = root / "data/problems/another-category/counter"
    duplicate.mkdir(parents=True)
    (duplicate / "spec.md").write_text(SPEC)
    output = tmp_path / "ambiguous"
    with pytest.raises(ValueError, match="ambiguous.*counter"):
        native_release.prepare(root, output, [tasks], workers=1)
    assert not output.exists() and not staged


def test_unselected_duplicate_does_not_block_explicit_selection(tmp_path, monkeypatch):
    root, task, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    for category in ("first", "second"):
        unused = root / "data/problems" / category / "unselected"
        unused.mkdir(parents=True)
        (unused / "spec.md").write_text(SPEC)
    assert native_release.selected_tasks(root, [tasks]) == (task,)


def test_empty_selection_is_not_a_native_experiment_release(tmp_path, monkeypatch):
    root, _, tasks, _, staged = task_fixture(tmp_path, monkeypatch)
    tasks.write_text("\n  \n")
    output = tmp_path / "empty"
    with pytest.raises(ValueError, match="at least one task"):
        native_release.prepare(root, output, [tasks], workers=1)
    assert not output.exists() and not staged


def test_native_selection_preserves_list_order_and_refuses_overlaps(tmp_path, monkeypatch):
    root, task, first, _, _ = task_fixture(tmp_path, monkeypatch)
    other = root / "data/problems/fixture/another"
    other.mkdir()
    (other / "spec.md").write_text(SPEC)
    second = tmp_path / "more.txt"
    second.write_text("another\n")
    assert native_release.selected_tasks(root, [second, first]) == (other, task)
    second.write_text("another\ncounter\n")
    with pytest.raises(ValueError, match="overlap"):
        native_release.selected_tasks(root, [first, second])


def test_selection_bytes_are_captured_before_elaboration(tmp_path, monkeypatch):
    root, _, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    original = native_release.api.contract_of_task
    selected = tasks.read_bytes()

    def contract(directory):
        tasks.write_text("changed-after-selection\n")
        return original(directory)

    monkeypatch.setattr(native_release.api, "contract_of_task", contract)
    result = native_release.prepare(root, tmp_path / "prepared", [tasks], workers=1)
    objects = DirectoryStore(tmp_path / "prepared/objects")
    release = ReleaseManifest.from_bytes(objects.read(BlobRef.from_dict(result["release"])))
    selection = parse_json(objects.read(release.policy))["split_lists"][tasks.name]
    assert objects.read(BlobRef.from_dict(selection)) == selected
    assert [task.task_id for task in release.tasks] == ["counter"]


def test_same_named_selection_files_do_not_silently_overwrite_provenance(tmp_path, monkeypatch):
    root, _, first, files, staged = task_fixture(tmp_path, monkeypatch)
    second = tmp_path / "another" / first.name
    second.parent.mkdir()
    second.write_text("second\n")
    task = root / "data/problems/fixture/second"
    task.mkdir()
    for name, raw in files.items():
        (task / name).write_bytes(raw)
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="list names"):
        native_release.prepare(root, output, [first, second], workers=1)
    assert not output.exists() and not staged


def test_publication_excludes_unreferenced_scratch_objects(tmp_path, monkeypatch):
    root, _, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    unrelated = DirectoryStore(scratch / "objects").put(b"UNRELATED_OLD_PRIVATE_OBJECT")
    output = tmp_path / "prepared"
    result = native_release.prepare(root, output, [tasks], workers=1, scratch=scratch)
    assert not (output / "objects" / unrelated.sha256).exists()
    assert (scratch / "objects" / unrelated.sha256).is_file(), "caller-owned scratch is retained"
    objects, reference = DirectoryStore(output / "objects"), BlobRef.from_dict(result["release"])
    loaded = load_published_task(objects, reference, "counter")
    assert loaded.manifest.archive_files
    selection = parse_json(objects.read(loaded.release.policy))["split_lists"]
    expected = {reference, loaded.release.policy, loaded.public.revision,
                loaded.manifest.run_policy, loaded.manifest.judge_policy,
                *(file.content for file in task_files(loaded.manifest)),
                *(BlobRef.from_dict(ref) for ref in selection.values())}
    assert {path.name for path in objects.directory.iterdir()} == {ref.sha256 for ref in expected}


def test_publication_failure_never_commits_a_release_head(tmp_path, monkeypatch):
    root, task, tasks, files, _ = task_fixture(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    transfers = []
    publish = DirectoryStore.publish_owned

    def fail(destination, source, references, **kwargs):
        assert destination.directory == output / "objects"
        transfers.append(references)
        publish(destination, source, references[:2], **kwargs)
        raise OSError("publication interrupted")

    monkeypatch.setattr(DirectoryStore, "publish_owned", fail)
    with pytest.raises(OSError, match="publication interrupted"):
        native_release.prepare(root, output, [tasks], workers=1)
    assert transfers and not (output / "release.json").exists()
    assert (output / "inventory.json").is_file()
    assert len(list((output / "objects").iterdir())) == 2
    assert {name: (task / name).read_bytes() for name in files} == files


def test_native_freeze_obeys_shared_scratch_root(tmp_path, monkeypatch):
    root, _, tasks, _, staged = task_fixture(tmp_path, monkeypatch)
    scratch = tmp_path / "node-local"
    scratch.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch))
    native_release.prepare(root, tmp_path / "prepared", [tasks], workers=1)
    assert staged and all(path.is_relative_to(scratch) for path in staged)
    assert all(not path.exists() for path in staged)


def test_selection_refuses_special_files_without_waiting(tmp_path):
    fifo = tmp_path / "task-ids.pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        native_release.selected_task_ids([fifo])


@pytest.mark.parametrize("placement", ["same", "inside", "parent"])
def test_explicit_scratch_cannot_overlap_published_tree(tmp_path, monkeypatch, placement):
    root, _, tasks, _, staged = task_fixture(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    scratch = {"same": output, "inside": output / "scratch", "parent": tmp_path}[placement]
    with pytest.raises(ValueError, match="must not overlap"):
        native_release.prepare(root, output, [tasks], workers=1, scratch=scratch)
    assert not output.exists() and not staged


def test_release_pointer_never_overwrites_another_commit(tmp_path, monkeypatch):
    root, _, tasks, _, _ = task_fixture(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    publish = DirectoryStore.publish_owned
    other = b'{"other":"retained commit"}'

    def concurrent_commit(destination, source, references, **kwargs):
        publish(destination, source, references, **kwargs)
        (output / "release.json").write_bytes(other)

    monkeypatch.setattr(DirectoryStore, "publish_owned", concurrent_commit)
    with pytest.raises(ValueError, match="refusing to replace"):
        native_release.prepare(root, output, [tasks], workers=1)
    assert (output / "release.json").read_bytes() == other


NATIVE_FREEZE_PROBE = r'''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.data import native_release
from behave.data.published import load_published_task

root = Path(sys.argv[1])
task = root / 'checkout/data/problems/fixture/counter'
task.mkdir(parents=True)
(task / 'spec.md').write_text('# Counter\n\n## Interface\n\n### Behavior interface\n\n```python\nclass Counter: ...\n```\n')
(task / 'behavior.py').write_text("raise AssertionError('reference must not execute')\n")
(task / 'LICENSE').write_bytes(b'PRIVATE_FIXTURE_NOT_LICENSE_APPROVAL')
selection = root / 'tasks.txt'
selection.write_bytes(b'counter\n')
scratch = root / 'scratch'
scratch.mkdir()
os.environ['BEHAVE_SCRATCH_DIR'] = str(scratch)
seen, foreign = [], []
def elaborate(directory):
    assert directory.is_relative_to(scratch)
    seen.append(directory)
    foreign.append(DirectoryStore(directory.parents[2] / 'objects').put(b'UNRELATED_SCRATCH_DATA'))
    selection.write_text('changed-after-selection\n')
    return SimpleNamespace(as_dict=lambda: {'fixture': 'contract'})
output = root / 'release'
with patch.object(native_release.api, 'contract_of_task', elaborate):
    result = native_release.prepare(root / 'checkout', output, [selection], workers=1)
assert seen and all(not path.exists() for path in seen)
assert not (output / 'objects' / foreign[0].sha256).exists()
objects = DirectoryStore(output / 'objects')
loaded = load_published_task(objects, BlobRef.from_dict(result['release']), 'counter')
policy = parse_json(objects.read(loaded.release.policy))
assert objects.read(BlobRef.from_dict(policy['split_lists']['tasks.txt'])) == b'counter\n'
assert {f.reference.path for f in loaded.public.files} == {'spec.md', 'contract.json'}
assert 'LICENSE' in {f.path for f in loaded.manifest.archive_files}
assert not any(name == 'tools' or name.startswith('tools.') for name in sys.modules)
print('native-freeze-installed')
'''
