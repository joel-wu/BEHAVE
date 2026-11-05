"""Checkpoint metadata and cold archives work without repository launchers."""

import subprocess
import sys

import pytest


CHECKPOINT_HANDOFF_PROBE = '''
import pathlib, sys
mode = sys.argv[1]
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, FileRef
from behave.runtime import models
job = pathlib.Path('standalone-checkpoint-' + mode).absolute()
hot = job / 'artifacts/plan/hf_4'
hot.mkdir(parents=True)
for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json'):
    (hot / name).write_text('{}')
(hot / 'model.safetensors').write_bytes(b'fixture weights, never deserialized')
journal = JournalDirectory(job / 'artifacts/plan/rounds/native').journal('rounds')
journal.append([], 'checkpoint_saved', {'round': 4})
handoff = models.periodic_handoff(job, 5, materialize=False)
assert handoff['step'] == 4 and handoff['job'] == str(job)
legacy = FileRef('fixture.bin', BlobRef.of(b'abc'))
assert models.CheckpointFile.from_dict(legacy.as_dict()).as_dict() == {
    'path': 'fixture.bin', 'size_bytes': 3}
receipt = models.publish_model(hot, handoff['weights']['files'],
    checkpoint=handoff['checkpoint'], staging=job / 'transport', destination=job / 'cold')
assert models.evict_model(hot, receipt, protected=[hot]) is False
assert models.evict_model(hot, receipt) is True
assert not hot.exists()
assert models.periodic_handoff(job, 5, materialize=False) == handoff
assert not hot.exists()
if mode == 'eval':
    from behave.eval import checkpoint as checkpoint_module
    assert checkpoint_module.checkpoint_ready(job, 5)
    assert not checkpoint_module.checkpoint_ready(job, 6)
    assert checkpoint_module.resolve_checkpoint({'jobs': [str(job)], 'update': 5}) == handoff
    assert not hot.exists()
    restored = checkpoint_module.stage_weights(handoff, job / 'private')
else:
    restored = models.restore_model(hot, destination=job / 'private')
assert (restored / 'model.safetensors').read_bytes() == b'fixture weights, never deserialized'
assert not hot.exists() and pathlib.Path(receipt['archive']).is_file()
assert not list((job / 'transport').iterdir())
'''


@pytest.mark.parametrize('consumer', ['runtime', 'eval'])
def test_shared_checkpoint_roundtrip_without_training(tmp_path, consumer):
    process = subprocess.run([sys.executable, '-I', '-B', '-c', CHECKPOINT_HANDOFF_PROBE, consumer],
                             cwd=tmp_path, capture_output=True, timeout=30, check=False)
    assert process.returncode == 0, process.stderr.decode(errors='replace')


