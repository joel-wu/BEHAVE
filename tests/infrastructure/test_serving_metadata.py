"""Model metadata must be regular input, including before GPU startup."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAM = r'''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.isolation.service import ServiceResult
from behave.serving import environment
from behave.serving.model_files import inspect_model_files
from model_service_job import native_endpoint
model, name = Path(sys.argv[2]), sys.argv[3]
environment.run_service = lambda *a, **kw: ServiceResult(
    'exited', 0, b'{"runtime":"fixture","torch":"fixture"}', b'', 0.)
spec = SimpleNamespace(model_path=str(model), python=sys.executable, runtime='vllm',
                       package_version='fixture', torch_version='fixture',
                       tool_parser='fixture', reasoning_parser='fixture')
endpoint = LocalChatEndpoint('http://127.0.0.1:31000/v1/chat/completions', 'fixture', 'fixture/1')
try:
    if name == 'config.json':
        environment.inspect_installation(spec, model, {}, deadline=1)
    elif name == 'model.safetensors.index.json':
        inspect_model_files(model)
    else:
        native_endpoint(endpoint, spec, DirectoryStore(model / 'objects'), checkpoint=BlobRef.of(b'fixture'))
except (ValueError, OSError):
    print('refused')
else:
    raise AssertionError('bad metadata accepted')
'''


@pytest.mark.parametrize("name", ["config.json", "model.safetensors.index.json", "tokenizer.json",
                                  "tokenizer_config.json", "chat_template.jinja"])
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_model_metadata_cannot_be_a_fifo_or_directory(tmp_path, name, kind):
    for initial in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (tmp_path / initial).write_bytes(b'{"chat_template":"fixture"}')
    (tmp_path / "model.safetensors").write_bytes(b"fake tensors")
    target = tmp_path / name
    target.unlink(missing_ok=True)
    if kind == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()
    tools = Path(__file__).resolve().parents[2] / "tools"
    result = subprocess.run([sys.executable, "-B", "-c", PROGRAM, str(tools), str(tmp_path), name],
                            capture_output=True, check=False, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == b"refused\n" and result.stderr == b""
