"""Execute actual launcher bootstraps without Slurm, models or installed fallback."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from behave.runtime import jobs, snapshots
from behave.runtime.records import write_json

REPO = Path(__file__).resolve().parents[2]


def frozen_source(tmp_path, *, legacy=False):
    source, target = tmp_path / 'input', tmp_path / 'trial/snapshot'
    files = {
        'src/behave/__init__.py': '',
        'src/behave/marker.py': 'VALUE = "frozen"\n',
        # Stage loads marker from the shared snapshot. Worker must reload it
        # from the verified local copy, not inherit Python's module cache.
        'src/behave/runtime/__init__.py': 'from behave import marker\n',
        'src/behave/runtime/records.py': (REPO / 'src/behave/runtime/records.py').read_text(),
        'data/splits/current.json': '{"authored": "fixture"}',
        'data/splits/fixture/eval.txt': 'one\n',
        'data/splits/fixture/external_eval.txt': 'two\n',
    }
    stager = (REPO / 'src/behave/runtime/snapshots.py').read_text()
    if legacy:
        files['tools/infra_validation_job.py'] = stager
    else:
        files['src/behave/runtime/snapshots.py'] = stager
        files['tools/infra_validation_job.py'] = 'from behave.runtime.snapshots import stage\n'
    worker = '''import json
from pathlib import Path
from behave import marker

def record(root, *arguments):
    Path(root, 'worker.json').write_text(json.dumps({
        'marker': marker.VALUE, 'module': marker.__file__, 'entry': __file__,
        'arguments': [str(arg) for arg in arguments]}))
    return 0
'''
    files.update({
        'tools/eval_review_trial.py': worker + '\ndef run(root):\n    return record(root)\n',
        'tools/pipeline_campaign.py': worker + '\ndef run(root, model, method, source):\n    return record(root, model, method, source)\n',
        'tools/pipeline_review_trial.py': worker + '\ndef node_run(root, source):\n    return record(root, source)\n',
    })
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    snapshots.freeze(source, target, files=tuple(files), trees=('src/behave',))
    return target


def command(root, name, monkeypatch):
    from tools import eval_review_trial, pipeline_campaign, pipeline_review_trial

    commands = []
    monkeypatch.setattr(jobs, 'submit', lambda control, argv, **kw: commands.append(argv))
    if name == 'review':
        write_json(root / 'settings.json', {'cpus': 37, 'tasks': ['one'], 'model': 'fixture'})
        eval_review_trial.submit(root, 123, snapshot=root / 'snapshot')
    elif name == 'pipeline':
        commands.append(pipeline_campaign.slurm_command(root, {'allocation': 123, 'cpus_per_arm': 37},
                                                        'fixture', 'joint'))
    else:
        write_json(root / 'configuration.json', {'allocation': 123, 'cpus': 37, 'methods': ['joint']})
        pipeline_review_trial.launch(root)
    argv, = commands
    assert '--cpus-per-task=37' in argv and '--gres=none' in argv
    return argv[argv.index(sys.executable):]


def execute(argv, tmp_path):
    # Keep any retained staging directories inside this test's scratch tree.
    # -S ensures launch cannot silently borrow the editable installation.
    boot = argv.index('-c') + 1
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    prefix = ('import tempfile; _mkdtemp=tempfile.mkdtemp; '
              'tempfile.mkdtemp=lambda suffix=None,prefix=None,dir=None: '
              f'_mkdtemp(suffix=suffix,prefix=prefix,dir={str(scratch)!r});\n')
    argv[boot] = prefix + argv[boot]
    result = subprocess.run([argv[0], '-S', *argv[1:]], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15, check=False)
    return result, scratch


@pytest.mark.parametrize('legacy', [False, True], ids=['shared-stager', 'historical-stager'])
@pytest.mark.parametrize('name', ['review', 'pipeline', 'pipeline-review'])
def test_launch_uses_only_verified_node_local_modules(tmp_path, monkeypatch, name, legacy):
    snapshot = frozen_source(tmp_path, legacy=legacy)
    root = snapshot.parent
    argv = command(root, name, monkeypatch)
    result, scratch = execute(argv, tmp_path)
    assert result.returncode == 0, result.stderr
    record = json.loads((root / 'worker.json').read_text())
    entry = Path(record['entry'])
    local = entry.parents[1]
    assert local.is_relative_to(scratch)
    assert Path(record['module']) == local / 'src/behave/marker.py'
    assert record['marker'] == 'frozen'
    assert record['arguments'] == {
        'review': [],
        'pipeline': ['fixture', 'joint', str(local)], 'pipeline-review': [str(local)],
    }[name]
    assert local.exists() is (name != 'review')  # Existing scratch lifetime.
    snapshots.verify(snapshot)


@pytest.mark.parametrize('name', ['review', 'pipeline', 'pipeline-review'])
def test_changed_snapshot_never_starts_worker(tmp_path, monkeypatch, name):
    snapshot = frozen_source(tmp_path)
    argv = command(snapshot.parent, name, monkeypatch)
    path = snapshot / 'src/behave/marker.py'
    path.chmod(0o644)
    path.write_text('VALUE = "changed after launch"\n')
    result, _ = execute(argv, tmp_path)
    assert result.returncode != 0 and 'differs from its frozen inventory' in result.stderr
    assert not (snapshot.parent / 'worker.json').exists()


@pytest.mark.parametrize('temporary', [False, True])
def test_shared_entry_preserves_exit_code_and_staging_lifetime(tmp_path, temporary):
    snapshot = frozen_source(tmp_path)
    argv = snapshots.staged_command(snapshot,
        "from tools.eval_review_trial import run; run(Path(sys.argv[1])); raise SystemExit(23)",
        snapshot.parent, temporary=temporary)
    result, scratch = execute(argv, tmp_path)
    assert result.returncode == 23, result.stderr
    record = json.loads((snapshot.parent / 'worker.json').read_text())
    local = Path(record['entry']).parents[1]
    assert local.is_relative_to(scratch) and local.exists() is not temporary


def test_building_command_does_not_read_source_or_dispatch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('command construction must be read/dispatch free')

    monkeypatch.setattr(Path, 'is_file', forbidden)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    argv = snapshots.staged_command(tmp_path / 'not yet mounted', 'raise SystemExit(0)', 'arg with spaces')
    assert argv[-2:] == [str(tmp_path / 'not yet mounted'), 'arg with spaces']


def test_entry_passes_literal_arguments_without_a_shell(tmp_path):
    snapshot = frozen_source(tmp_path / 'source with spaces')
    argument = "literal 'quotes' ; $(not-a-command)"
    argv = snapshots.staged_command(snapshot,
        "import json; print(json.dumps(sys.argv[1:]))", argument, temporary=True)
    result, _ = execute(argv, tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [argument]
