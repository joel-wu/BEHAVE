"""Installed plan preparation shares the source-launcher's selection boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_slime_training_plan import inputs  # noqa: F401 -- shared fixture

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.records import RecordError
from behave.train import plan


def repository(tmp_path, *, eval_task="evaluation", external_task="external"):
    root = tmp_path / "repository"
    split = root / "data/splits/current"
    split.mkdir(parents=True)
    (split.parent / "current.json").write_text('{"authored":"current"}')
    (split / "eval.txt").write_text(eval_task + "\n")
    (split / "external_eval.txt").write_text(external_task + "\n")
    return root


@pytest.mark.parametrize("task", ["evaluation", "external"])
def test_explicit_repository_rejects_leakage_before_any_preparation(tmp_path, task):
    root = repository(tmp_path)
    stale = tmp_path / "stale.txt"
    stale.write_text(task + "\n")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="current evaluation tasks"):
        plan.prepare_from_release(output, {}, tmp_path / "absent", [task], stale,
                                            repository=root)
    assert not output.exists()


def test_installed_boundary_has_no_ambient_repository(tmp_path, monkeypatch):
    split = tmp_path / "train.txt"
    split.write_text("training\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError, match="repository"):
        plan.check_training_selection(["training"], split)
    with pytest.raises(FileNotFoundError):
        plan.check_training_selection(["training"], split, repository=tmp_path / "absent")


@pytest.mark.parametrize("router", ["direct", "infra"])
def test_public_prepare_reuses_legacy_plan_and_split_bytes(tmp_path, inputs, capsys, router):  # noqa: F811
    from behave.cli.__main__ import main as infra_main
    from behave.train.__main__ import main as train_main
    from tools.slime_training_plan import prepare

    configuration, release, split, task = inputs
    root = repository(tmp_path)
    config_path = tmp_path / "configuration.json"
    config_path.write_text(json.dumps(configuration))
    output = tmp_path / "prepared"
    args = ["prepare", str(output), "--repository", str(root), "--configuration", str(config_path),
            "--release", str(release), "--train-split", str(split), "--task", task]
    assert (train_main(args) if router == "direct" else infra_main(["train", *args])) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"binding": str(output / "binding.json"), "native_training_started": False}
    original = (output / "binding.json").read_bytes()
    source_receipt = (output / "source_split.json").read_bytes()
    binding = prepare(output, configuration, release, [task], split, repository=root)
    assert (output / "binding.json").read_bytes() == original
    assert (output / "source_split.json").read_bytes() == source_receipt
    assert DirectoryReader(binding["objects"]).read(BlobRef.from_dict(json.loads(source_receipt))) == split.read_bytes()
    assert not (output / "rounds").exists()


@pytest.mark.parametrize("missing", ["--repository", "--parent-checkpoint"])
def test_public_prepare_requires_explicit_repository_and_complete_parent(tmp_path, monkeypatch, missing):
    from behave.cli import training_plan

    monkeypatch.setattr(training_plan, "prepare_from_release", lambda *a, **k: pytest.fail("no preparation"))
    args = [str(tmp_path / "output"), "--configuration", "config", "--release", "release",
            "--train-split", "split", "--task", "training"]
    if missing == "--parent-checkpoint":
        args.extend(["--repository", str(tmp_path), "--parent-objects", "objects"])
    with pytest.raises(SystemExit) as error:
        training_plan.main(args)
    assert error.value.code == 2 and not (tmp_path / "output").exists()


@pytest.mark.parametrize("record", ["split-index", "release"])
def test_release_preparation_rejects_duplicate_record_keys(tmp_path, inputs, record):  # noqa: F811
    configuration, release, split, task = inputs
    root = repository(tmp_path)
    path = root / "data/splits/current.json" if record == "split-index" else release / "release.json"
    original = json.loads(path.read_text())
    key, value = next(iter(original.items()))
    payload = path.read_bytes().rstrip()[:-1] + b"," + json.dumps(key).encode() + b":" + json.dumps(value).encode() + b"}"
    path.write_bytes(payload)
    output = tmp_path / "output"
    with pytest.raises(RecordError) as error:
        plan.prepare_from_release(output, configuration, release, [task], split, repository=root)
    assert error.value.code == "record-key-duplicate"
    assert not output.exists() and path.read_bytes() == payload


def test_missing_current_split_has_an_input_error_not_keyerror(tmp_path):
    root = repository(tmp_path)
    (root / "data/splits/current.json").write_text('{}')
    with pytest.raises(RecordError) as error:
        plan.current_path(repository=root)
    assert error.value.code == "training-split-invalid"


def test_selected_task_missing_from_release_never_prepares(tmp_path, inputs, monkeypatch):  # noqa: F811
    configuration, release, split, _ = inputs
    root = repository(tmp_path)
    split.write_text("unpublished-task\n")
    monkeypatch.setattr("behave.train.slime_data.prepare_task_plan",
                        lambda *a, **k: pytest.fail("unpublished selection reached plan preparation"))
    with pytest.raises(RecordError) as error:
        plan.prepare_from_release(tmp_path / "output", configuration, release,
                                            ["unpublished-task"], split, repository=root)
    assert error.value.code == "training-task-unpublished"
    assert "unpublished-task" in str(error.value)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("invalid_parent", [False, True])
def test_parent_input_is_read_only_even_when_preparation_fails(tmp_path, monkeypatch, capsys, invalid_parent):
    from behave.cli import training_plan

    config = tmp_path / "configuration.json"
    config.write_text('{}')
    parent = tmp_path / "parent.json"
    reference = BlobRef.of(b"parent checkpoint")
    parent.write_text(json.dumps(reference.as_dict()))
    if invalid_parent:
        parent.write_bytes(parent.read_bytes()[:-1] + b',"size_bytes":17}')
    objects, output = tmp_path / "missing-parent-objects", tmp_path / "output"
    seen = []

    def prepare(*a, parent_reader, parent_checkpoint, **kw):
        assert not invalid_parent, "invalid parent reached preparation"
        assert type(parent_reader) is DirectoryReader
        assert not hasattr(parent_reader, "put")
        assert parent_checkpoint == reference
        seen.append(parent_checkpoint)
        parent_reader.read(parent_checkpoint)

    monkeypatch.setattr(training_plan, "prepare_from_release", prepare)
    args = [str(output), "--repository", str(tmp_path), "--configuration", str(config),
            "--release", "release", "--train-split", "train.txt", "--task", "training",
            "--parent-objects", str(objects), "--parent-checkpoint", str(parent)]
    assert training_plan.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable"
    assert result["code"] == ("record-key-duplicate" if invalid_parent else "content-unavailable")
    assert result["message"]
    assert len(seen) == (0 if invalid_parent else 1)
    assert not objects.exists() and not output.exists()


def test_public_prepare_imports_a_real_sealed_parent_read_only(tmp_path, inputs, monkeypatch, capsys):  # noqa: F811
    from test_slime_stage import native_worker, prepared

    from behave.cli.training_plan import main
    from behave.train.slime_stage import run_learning
    from behave.train.trainer import NativeAnchor

    before, configuration = prepared(tmp_path, monkeypatch, inputs, "task")
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(before, configuration))
    report = run_learning(**before)
    parent = BlobRef.from_dict(report["checkpoint"])
    source = before["run"].objects.directory
    retained = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    _, release, split, task = inputs
    config_path, parent_path = tmp_path / "config.json", tmp_path / "parent.json"
    config_path.write_text(json.dumps(configuration))
    parent_path.write_text(json.dumps(parent.as_dict()))
    output = tmp_path / "next-plan"
    assert main([str(output), "--repository", str(repository(tmp_path)),
                 "--configuration", str(config_path), "--release", str(release),
                 "--train-split", str(split), "--task", task, "--parent-objects", str(source),
                 "--parent-checkpoint", str(parent_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["native_training_started"] is False
    binding = json.loads((output / "binding.json").read_text())
    assert binding["parent_checkpoint"] == parent.as_dict()
    assert NativeAnchor.load(DirectoryReader(binding["objects"]), parent) == NativeAnchor.load(DirectoryReader(source), parent)
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == retained
    assert not (output / "rounds").exists()


@pytest.mark.parametrize("router", ["direct", "infra", "legacy"])
@pytest.mark.parametrize("invalid", ["missing", "duplicate"])
def test_prepare_entrypoints_report_input_errors_without_preparing(tmp_path, capsys, monkeypatch, router, invalid):
    from behave.cli import training_plan
    from behave.cli.__main__ import main as infra_main
    from behave.train.__main__ import main as train_main
    from tools.slime_training_plan import main as legacy_main

    monkeypatch.setattr(training_plan, "prepare_from_release", lambda *a, **k: pytest.fail("invalid config prepared"))
    config = tmp_path / "config.json"
    if invalid == "duplicate":
        config.write_text('{"initial_updates":3,"initial_updates":60}')
    output = tmp_path / "output"
    args = [str(output), "--repository", str(tmp_path), "--configuration", str(config),
            "--release", "release", "--train-split", "train.txt", "--task", "training"]
    result = (train_main(["prepare", *args]) if router == "direct" else
              infra_main(["train", "prepare", *args]) if router == "infra" else legacy_main(args))
    assert result == 1
    captured = capsys.readouterr()
    error = json.loads(captured.out)
    assert error["status"] == "unavailable" and error["message"]
    assert error["code"] == ("record-key-duplicate" if invalid == "duplicate" else "training-prepare-invalid")
    assert not captured.err and not output.exists()


# Run unchanged against both the source package and a non-editable wheel. FIFOs
# must fail promptly, not hang an operator command waiting for an input writer.
TRAINING_INPUT_PROBE = r'''
import contextlib, io, json, os, sys
from pathlib import Path
from behave.cli.training_plan import main
from behave.train.plan import load_configuration
from behave.train.plan import check_training_selection, prepare_from_release

root = Path(sys.argv[1])
root.mkdir()
fifo = root / 'input.fifo'
os.mkfifo(fifo)
for machine in (None, fifo):
    config = fifo
    if machine is not None:
        config = root / 'experiment.json'
        config.write_text('{"schema":"behave.training-experiment/1","settings":{}}')
    try:
        load_configuration(config, machine_profile=machine)
    except ValueError as error:
        assert str(error) == 'operator input must be a regular file'
    else:
        raise AssertionError('special configuration was read')

split = root / 'train.txt'
split.write_text('training\n')
current = root / 'data/splits/current'
current.mkdir(parents=True)
index = current.parent / 'current.json'
index.write_text('{"authored":"current"}')
(current / 'eval.txt').write_text('evaluation\n')
(current / 'external_eval.txt').write_text('external\n')
for path in (split, index, current / 'eval.txt', current / 'external_eval.txt'):
    saved = path.read_bytes()
    path.unlink()
    path.symlink_to(fifo.absolute())
    try:
        check_training_selection(['training'], split, repository=root)
    except ValueError as error:
        assert str(error) == 'operator input must be a regular file'
    else:
        raise AssertionError('special split input was read')
    path.unlink()
    path.write_bytes(saved)

# Ordinary operator symlinks remain supported, and the original split bytes survive.
alias = root / 'train-alias.txt'
alias.symlink_to(split.absolute())
assert check_training_selection(['training'], alias, repository=root) == split.read_bytes()
release = root / 'release'
release.mkdir()
(release / 'release.json').symlink_to(fifo.absolute())
try:
    prepare_from_release(root / 'output', {}, release, ['training'], split, repository=root)
except ValueError as error:
    assert str(error) == 'operator input must be a regular file'
else:
    raise AssertionError('special release receipt was read')
config = root / 'flat.json'
config.write_text('{}')
args = [str(root / 'output'), '--repository', str(root), '--configuration', str(config),
        '--release', str(release), '--train-split', str(split), '--task', 'training',
        '--parent-objects', str(root / 'parent-objects'), '--parent-checkpoint', str(fifo)]
stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    assert main(args) == 1
result = json.loads(stdout.getvalue())
assert result == {'status': 'unavailable', 'code': 'training-prepare-invalid',
                  'message': 'operator input must be a regular file'}
assert not (root / 'output').exists() and not (root / 'parent-objects').exists()
'''


def test_operator_inputs_never_wait_on_special_files(tmp_path):
    source = Path(plan.__file__).resolve().parents[2]
    code = f"import sys; sys.path.insert(0, {str(source)!r})\n" + TRAINING_INPUT_PROBE
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(tmp_path / "probe")],
                               capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
