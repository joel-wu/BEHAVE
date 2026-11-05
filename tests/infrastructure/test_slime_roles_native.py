"""Use the pinned native argument parsers, not a fake local parser."""

import time
from pathlib import Path

import pytest
from test_native_torch import native_process

from behave.isolation.service import run_service
from behave.train.slime_launch import environment

pytestmark = [pytest.mark.native_training, pytest.mark.isolation]


def test_native_capacity_shape_only_advantage_input(tmp_path):
    spec = native_process()
    env = {**spec.environment, **environment(Path(spec.argv[0]).resolve().parents[1], tmp_path,
                                            cuda_visible_devices="0")}
    env["CUDA_VISIBLE_DEVICES"] = ""
    code = '''
from types import SimpleNamespace
from unittest.mock import patch
import torch
from slime.backends.megatron_utils.loss import compute_advantages_and_returns
args = SimpleNamespace(use_rollout_logprobs=False, kl_coef=0, custom_advantage_function_path=None,
    advantage_estimator='grpo', use_opd=False, normalize_advantages=False)
with patch('megatron.core.mpu.is_pipeline_last_stage', return_value=True):
    for placeholder in (0., -37.):
        # Includes an empty CP-local response shard, as occurs for long prompts.
        data = dict(rollout_log_probs=[torch.full((1024,), placeholder), torch.empty(0)],
                    rewards=[-1., 1.], response_lengths=[1024, 1024], total_lengths=[65536, 65536])
        compute_advantages_and_returns(args, data)
        assert [len(x) for x in data['advantages']] == [1024, 0]
        assert torch.equal(data['advantages'][0], torch.full((1024,), -1.))
        assert all(not torch.count_nonzero(x) for x in data['kl'])
assert not torch.cuda.is_initialized()
print('BEHAVE_CAPACITY_SHAPE_OK')
'''
    result = run_service((spec.argv[0], "-B", "-c", code), cwd=tmp_path, environment=env,
                         network="none", deadline=time.monotonic() + 180, output_bytes=1 << 20)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
        result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
    assert b"BEHAVE_CAPACITY_SHAPE_OK" in result.stdout


def test_native_cuda_loader_uses_explicit_wheel_libraries(tmp_path, record_testsuite_property):
    spec = native_process()
    env = {**spec.environment, **environment(Path(spec.argv[0]).resolve().parents[1], tmp_path,
                                            cuda_visible_devices="0")}
    # Import-only CPU test: no device is exposed and no CUDA context is created.
    env["CUDA_VISIBLE_DEVICES"] = ""
    code = '''
import json, os, time
from pathlib import Path
started = time.monotonic()
import transformer_engine.common as te
import torch
assert not torch.cuda.is_initialized()
paths = {}
for key, handle in [('CUDNN', te._CUDNN_LIB_CTYPES), ('NVRTC', te._NVRTC_LIB_CTYPES), ('CURAND', te._CURAND_LIB_CTYPES)]:
    path = Path(handle._name).resolve()
    assert path.is_relative_to(Path(os.environ[key + '_HOME']).resolve())
    paths[key] = str(path)
print('BEHAVE_CUDA_LOADER ' + json.dumps({'seconds': time.monotonic() - started, 'libraries': paths}))
'''
    result = run_service((spec.argv[0], "-B", "-c", code), cwd=tmp_path, environment=env,
                         network="none", deadline=time.monotonic() + 180, output_bytes=1 << 20)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
        result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
    from behave.core.records import parse_json

    prefix = b"BEHAVE_CUDA_LOADER "
    (record,) = [parse_json(line[len(prefix):]) for line in result.stdout.splitlines() if line.startswith(prefix)]
    record_testsuite_property("cuda_loader", record)


def test_native_slime_role_and_router_configuration(tmp_path):
    spec = native_process()
    code = '''
import sys
import json
from pathlib import Path
from types import SimpleNamespace
from behave.agent.providers.sglang_tools import PARSER, register_qwen_tools
from behave.train.slime_launch import preset
from behave.train.slime_rollout import SlimeBatch
from behave.train.slime_launch import write_role_configuration
from behave.train.slime_hooks import STEP_HOOK, before_train_step
from behave.train.slime_hooks import configure_scheduler as configure
from behave.train.slime_checkpoint import checkpoint_due
from sglang.srt.plugins import load_plugins
from sglang.srt.function_call.function_call_parser import FunctionCallParser
from sglang.srt.function_call.qwen3_coder_detector import Qwen3CoderDetector
from sglang.srt.entrypoints.openai.protocol import Tool
from slime.backends.sglang_utils.arguments import sglang_parse_args
from slime.utils.arguments import parse_megatron_role_args
from slime.utils.misc import load_function, should_run_periodic_action
# The application has an explicit finite stopping round and no epoch count.
# Compare our journal guard against the actual pinned native save predicate.
for rounds in (1, 2, 7, 11):
    for interval in (1, 2, 3, 8, 20):
        cadence = SimpleNamespace(num_rollout=rounds, save_interval=interval)
        assert [checkpoint_due(cadence, i) for i in range(rounds)] == [
            should_run_periodic_action(i, interval, None, rounds) for i in range(rounds)]
load_plugins()  # Discover BEHAVE through the installed wheel, as engines do.
assert PARSER in FunctionCallParser.ToolCallParserEnum
register_qwen_tools()  # CLI preflight and native plugin loading are idempotent.
tools = [Tool(type='function', function={'name': 'submit', 'parameters': {
    'type': 'object', 'properties': {'artifact': {'anyOf': [
        {'type': 'object'}, {'type': 'null'}]}}}})]
xml = '<tool_call><function=submit><parameter=artifact>{"files":["x.py"],"flag":false,"extra":null}</parameter></function></tool_call>'
before = Qwen3CoderDetector().detect_and_parse(xml, tools)
after = FunctionCallParser.ToolCallParserEnum[PARSER]().detect_and_parse(xml, tools)
assert type(json.loads(before.calls[0].parameters)['artifact']) is str
assert json.loads(after.calls[0].parameters)['artifact'] == {'files': ['x.py'], 'flag': False, 'extra': None}
assert FunctionCallParser.ToolCallParserEnum['qwen3_coder'] is Qwen3CoderDetector
batch = SlimeBatch(({'rollout_id': 0, 'metadata': {'behave_prefix_cache': {
    'cached_tokens': 12, 'total_prompt_tokens': 16}}},
    {'rollout_id': 1, 'metadata': {}}),
    ({'reward': 1, 'excluded_turns': 0}, {'reward': 0, 'excluded_turns': 0}), preset('grpo'))
samples = batch.make_samples()
assert samples[0].prefix_cache_info.prefix_cache_hit_rate == 0.75
output = batch.rollout_output()
assert output.metrics['behave/cache_measured_turns'] == 1
assert output.metrics['behave/retained_turns'] == 2
sys.argv = ['native-config', *preset('ppo').arguments(), '--sglang-tool-call-parser', PARSER]
parsed = sglang_parse_args()
assert parsed.router_disable_retries is True
assert parsed.router_policy == 'consistent_hashing'
assert parsed.sglang_tool_call_parser == PARSER
root = Path.cwd()
args = SimpleNamespace(load=str(root / 'hf'), save=str(root / 'save'),
    use_critic=True, num_critic_only_steps=0, megatron_config_path=None,
    use_checkpoint_opt_param_scheduler=True, override_opt_param_scheduler=False)
configure(args)
write_role_configuration(args, root)
actor = parse_megatron_role_args(args, args.megatron_config_path, 'actor')
critic = parse_megatron_role_args(args, args.megatron_config_path, 'critic')
assert actor.save == args.save and critic.save == args.save + '/critic'
assert actor.load == critic.load == args.load
assert critic.untie_embeddings_and_output_weights is True
# Exercise the pinned role parser and callback resolver, not just a local
# Namespace: both roles must retain restoration policy and the public hook.
for role_args in (actor, critic):
    assert role_args.use_checkpoint_opt_param_scheduler is True
    assert role_args.override_opt_param_scheduler is False
    assert role_args.custom_megatron_before_train_step_hook_path == STEP_HOOK
    assert load_function(role_args.custom_megatron_before_train_step_hook_path) is before_train_step
for checkpoint in (Path(args.save), Path(args.save) / 'critic'):
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / 'latest_checkpointed_iteration.txt').write_text('1')
args.load, args.megatron_config_path = args.save, None
write_role_configuration(args, root)
for role in ('actor', 'critic'):
    resumed = parse_megatron_role_args(args, args.megatron_config_path, role)
    expected = Path(args.load) if role == 'actor' else Path(args.load) / 'critic'
    assert resumed.load == resumed.save == str(expected)
    assert resumed.use_checkpoint_opt_param_scheduler is True
    assert resumed.custom_megatron_before_train_step_hook_path == STEP_HOOK
print('BEHAVE_NATIVE_ROLES_OK', flush=True)
'''
    result = run_service((spec.argv[0], '-B', '-c', code), cwd=tmp_path,
        environment={**spec.environment, "SGLANG_PLUGINS": "behave_qwen_tools"},
        network='none', deadline=time.monotonic() + 300,
        output_bytes=1 << 20, cleanup_seconds=30)
    assert result.status == 'exited' and result.returncode == 0 and result.cleanup_state.quiescent, (
        result.stdout.decode(errors='replace'), result.stderr.decode(errors='replace'))
    assert b'BEHAVE_NATIVE_ROLES_OK' in result.stdout
