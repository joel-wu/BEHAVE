"""Native actor/critic evidence is independent of a successful process exit."""

import pytest
from support.slime_training_evidence import training_steps

from behave.train.slime_checkpoint import native_phase_timings, native_training_metrics


def test_native_phase_timings_only_use_completed_native_measurements():
    text = (
        "\x1b[36m(MegatronTrainRayActor pid=2909)\x1b[0m "
        "[2026-09-18 10:00:00] timer.py:32 - Timer actor_train end (elapsed: 12.3s)\n"
        "[2026-09-18 10:00:01] timer.py:24 - Timer save_model start\n"
        "[2026-09-18 10:00:04] timer.py:32 - Timer save_model end (elapsed: 2.9s)\n"
        "[2026-09-18 10:00:05] timer.py:24 - Timer update_weights start\n"
        "[2026-09-18 10:00:06] other.py:32 - Timer update_weights end (elapsed: 99.0s)\n")
    assert native_phase_timings(text) == [
        {"logged_at": "2026-09-18 10:00:00", "phase": "actor_train", "seconds": 12.3},
        {"logged_at": "2026-09-18 10:00:04", "phase": "save_model", "seconds": 2.9}]


def line(step=0, norm=2.0, role="actor"):
    prefix = "train/" if role == "actor" else "train/critic-"
    return f"step {step}: " + repr({"train/step": step, prefix + "grad_norm": norm}) + "\n"


def test_native_optimization_metrics_keep_zero_and_distinct_update_clocks():
    first = "[2026-09-18 10:00:00] model.py:918 - " + line(norm=0.)
    later = "[2026-09-18 10:01:00] model.py:918 - " + line(norm=0.)
    critic = "[2026-09-18 10:00:00] model.py:918 - " + line(role="critic")
    assert native_training_metrics(first + first + later + critic) == [
        {"logged_at": "2026-09-18 10:00:00", "train/step": 0, "train/grad_norm": 0.},
        {"logged_at": "2026-09-18 10:01:00", "train/step": 0, "train/grad_norm": 0.}]
    assert native_training_metrics("Waiting for rollout\n") == []


def test_training_evidence_separates_actor_critic_and_duplicate_logs():
    text = line(role="critic") + line() + line(1, 0.) + line()
    assert [r["train/step"] for r in training_steps(text)] == [0, 1]
    assert training_steps(text, role="critic") == [{"train/step": 0, "train/critic-grad_norm": 2.0}]
    with pytest.raises(ValueError, match="critic"):
        training_steps(line(), role="critic")
    assert len(training_steps(line() + line(norm=3.))) == 2  # Variable round sizes can reuse a native step ID.


@pytest.mark.parametrize("text", ["process exited zero", line(norm=0.), line(norm=-1.),
    line(norm=True), line(norm=float("inf")), line(step=-1)])
def test_training_evidence_rejects_absent_zero_or_invalid_rows(text):
    with pytest.raises(ValueError):
        training_steps(text)


def test_zero_gradient_is_recorded_without_claiming_an_update():
    assert training_steps(line(norm=0.), require_nonzero=False) == [
        {"train/step": 0, "train/grad_norm": 0.}]
    with pytest.raises(ValueError, match="evidence"):
        training_steps("process exited zero", require_nonzero=False)


@pytest.mark.isolation
def test_round_evidence_reads_real_supervised_fixture_reports(tmp_path, publication, monkeypatch):
    import shutil
    import sys

    from test_native_chat_tokens import native_training_run

    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json
    from behave.train.slime_checkpoint import scored_rounds
    from behave.train.slime_launch import preset

    run, manifest, _, _, endpoint = native_training_run(tmp_path, publication)
    head, _ = run.seal("training")
    root = tmp_path / "inspection"
    shutil.copytree(run.directory, root / "rounds/round_00000000")
    binding = {"schema": "behave.slime-rollout-binding/1", "control_python": sys.executable,
        "control_package": str(tmp_path), "objects": str(publication.root),
        "plan": BlobRef.of(b"fixture plan").as_dict(), "manifest": manifest.as_dict(),
        "endpoint": endpoint.as_dict(), "recipe": preset("reinforce_plus_plus").as_dict(),
        "run_directory": str(root / "rounds")}
    (root / "binding.json").write_bytes(canonical_json(binding))
    native = JournalDirectory(root / "rounds/native")
    receipt = native.objects.put(canonical_json({"round": 0, "manifest": manifest.as_dict(),
        "objects": str(root / "rounds/round_00000000/objects"),
        "head": head.as_dict(), "endpoint": endpoint.as_dict()}))
    native.journal("rounds").append((), "round_scored", {"round": 0, "receipt": receipt.as_dict()})
    rows = scored_rounds(root)
    # A window-boundary stop retains its generated prefix as a training target.
    assert rows[0]["round"] == 0 and rows[0]["retained_turns"] == 3
    assert rows[0]["max_sample_tokens"] == 15
    candidate, = rows[0]["candidates"]
    assert candidate["reward"] == 0 and not candidate["submitted"]
    assert candidate["rtl_score"] is candidate["behavior_score"] is None

    # Current live rounds already have compact metrics. Final acceptance must
    # reuse them rather than re-read and re-pack all native token responses.
    metrics = root / "rounds/round_00000000/metrics.json"
    metrics.write_bytes(canonical_json(rows[0]))

    def unexpected_reconstruction(*args, **kwargs):
        raise AssertionError("recorded metrics must not reconstruct token reports")

    monkeypatch.setattr("behave.train.slime_rollout.from_training_report", unexpected_reconstruction)
    assert scored_rounds(root) == rows


@pytest.mark.native_training
@pytest.mark.isolation
def test_numerical_weight_evidence_in_the_installed_native_abi(tmp_path):
    import time

    from test_native_torch import native_process

    from behave.isolation.service import run_service

    spec = native_process()
    script = '''
import json,sys
from pathlib import Path
import torch
from safetensors.torch import save_file
from behave.train.slime_checkpoint import changed_decoder_weight
root = Path(sys.argv[1])
name = "model.language_model.layers.0.self_attn.q_proj.weight"
before, after = root / "before", root / "after"
for path in (before, after):
    path.mkdir()
    (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {name: "model.safetensors"}}))
x = torch.tensor([[1., 2.], [3., 4.]], dtype=torch.bfloat16)
save_file({name: x}, str(before / "model.safetensors"))
save_file({name: x.clone()}, str(after / "model.safetensors"))
assert changed_decoder_weight(before, after, require_change=False) is None
try:
    changed_decoder_weight(before, after)
except ValueError:
    pass
else:
    raise AssertionError("identical weights were counted as an update")
y = x.clone(); y[0,0] = 1.5
save_file({name: y}, str(after / "model.safetensors"))
result = changed_decoder_weight(before, after)
assert result["changed_elements"] == 1 and result["max_abs_difference"] == .5
assert not torch.cuda.is_initialized()
print(json.dumps(result))
'''
    result = run_service((spec.argv[0], "-B", "-c", script, str(tmp_path)), cwd=tmp_path,
        environment=spec.environment, network="none", deadline=time.monotonic() + spec.wall_seconds)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, result.stderr
