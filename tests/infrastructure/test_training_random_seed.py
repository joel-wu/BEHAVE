import json
from copy import deepcopy

import pytest
from test_slime_training_plan import inputs  # noqa: F401

from behave.train.plan import resolve_training_seed
from behave.train.pool import SamplingPolicy


def configuration():
    return {"sampler": {"tasks_per_update": 4, "order": "shuffle", "seed": "old"},
            "native_arguments": ["--bf16", "--seed", "123", "--lr", "1e-6"],
            "sampling": {"temperature": 1}}


def test_one_seed_controls_sampler_and_native_engine_seed_without_touching_generation_profile():
    config = {**configuration(), "training_seed": 456}
    before = deepcopy(config)
    result = resolve_training_seed(config)
    assert result["sampler"]["seed"] == "456"
    assert result["native_arguments"] == ["--bf16", "--seed", "456", "--lr", "1e-6"]
    assert "seed" not in result["sampling"]
    assert config == before and resolve_training_seed(result) == result
    other = resolve_training_seed({**config, "training_seed": 789})
    tasks = tuple(f"task-{i}" for i in range(30))
    first = SamplingPolicy.from_dict(result["sampler"])
    second = SamplingPolicy.from_dict(other["sampler"])
    assert [first.select(tasks, i) for i in range(5)] != [second.select(tasks, i) for i in range(5)]
    assert first == SamplingPolicy.from_dict(json.loads(json.dumps(result))["sampler"])


@pytest.mark.parametrize("seed", [-1, True, 2**31, "123"])
def test_invalid_seed_fails_closed(seed):
    with pytest.raises(ValueError):
        resolve_training_seed({**configuration(), "training_seed": seed})


@pytest.mark.parametrize("args", [["--seed"], ["--seed", "--bf16"], ["--seed=3"],
                                  ["--seed", "1", "--seed", "2"]])
def test_ambiguous_native_seed_is_rejected(args):
    with pytest.raises(ValueError):
        resolve_training_seed({**configuration(), "training_seed": 1, "native_arguments": args})


def test_same_per_request_seed_is_not_imposed_on_eight_candidates():
    with pytest.raises(ValueError, match="per-request"):
        resolve_training_seed({**configuration(), "training_seed": 1, "sampling": {"seed": 1}})


def test_task_plan_records_resolved_seed_and_uses_it_in_native_launch(tmp_path, inputs):
    from behave.train.slime_launch import native_arguments
    from tools.slime_training_plan import prepare

    config, release, split, task = inputs
    config.update(training_seed=321,
                  sampler={"tasks_per_update": 1, "order": "shuffle", "seed": "old"})
    output = tmp_path / "seeded"
    binding = prepare(output, config, release, [task], split)
    assert binding["sampler"]["seed"] == "321"
    saved = json.loads((output / "configuration.json").read_text())
    assert saved["training_seed"] == 321
    argv = native_arguments(output, updates=2, load=tmp_path / "initial", cpus=8,
                            checkpoint_directory=tmp_path / "checkpoints")
    assert argv[argv.index("--seed") + 1] == "321"
