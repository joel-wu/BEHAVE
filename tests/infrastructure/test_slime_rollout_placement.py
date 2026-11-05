"""Skip unhealthy rollout GPU slots without changing Megatron placement."""

import json
from dataclasses import replace

import pytest
from test_slime_training_plan import inputs

from behave.train.slime_launch import SlimeLayout, native_arguments
from tools.slime_training_plan import prepare


@pytest.mark.parametrize("excluded", [5, 7])
@pytest.mark.parametrize("actor_gpus,cp", [(8, 2), (4, 1)])
def test_full_node_tp1_deployment_leaves_exact_slot_empty(tmp_path, inputs, excluded, actor_gpus, cp):
    config, release, split, task = inputs
    config = {**config, "workers": 28, "layout": {**config["layout"],
        "rollout_gpus_per_engine": 1, "rollout_excluded_gpus": [excluded],
        "actor_gpus_per_node": actor_gpus, "context_parallel": cp}}
    layout = SlimeLayout(**config["layout"])
    assert layout.required_gpus == 8 and layout.actor_gpus == actor_gpus
    assert layout.tensor_parallel == 4 and layout.context_parallel == cp
    assert layout.rollout_engine_count == 7
    output = tmp_path / "plan"
    binding = prepare(output, config, release, [task], split)
    argv = native_arguments(output, updates=4, load=tmp_path / "load", cpus=144,
                            checkpoint_directory=tmp_path / "save")
    assert argv[argv.index("--rollout-num-gpus") + 1] == "8"
    assert argv[argv.index("--rollout-num-gpus-per-engine") + 1] == "1"
    assert argv[argv.index("--actor-num-gpus-per-node") + 1] == str(actor_gpus)
    assert argv[argv.index("--context-parallel-size") + 1] == str(cp)
    document = json.loads((output / "rollout_deployment.json").read_text())
    slots = [g["worker_type"] for g in document["sglang"][0]["server_groups"] for _ in range(g["num_gpus"])]
    assert slots == ["placeholder" if i == excluded else "regular" for i in range(8)]
    assert document["sglang"][0]["update_weights"] is True
    assert binding["rollout_engine_count"] == 7
    assert native_arguments(output, updates=4, load=tmp_path / "load", cpus=144,
                            checkpoint_directory=tmp_path / "save") == argv
    (output / "rollout_deployment.json").chmod(0o600)
    (output / "rollout_deployment.json").write_text("{}")
    with pytest.raises(ValueError, match="deployment changed"):
        native_arguments(output, updates=4, load=tmp_path / "load", cpus=144,
                         checkpoint_directory=tmp_path / "save")


def test_exclusions_reject_ambiguous_or_empty_placement():
    layout = SlimeLayout(actor_gpus_per_node=8, gpus_per_node=8, tensor_parallel=4,
        context_parallel=2, rollout_gpus=8, rollout_gpus_per_engine=1)
    for excluded in ([8], [-1], [5, 5], [True], list(range(8)), "5"):
        with pytest.raises(ValueError):
            replace(layout, rollout_excluded_gpus=excluded)
    with pytest.raises(ValueError, match="full-node"):
        replace(layout, rollout_excluded_gpus=[5], rollout_gpus_per_engine=4)
    assert layout.deployment() is None
    assert layout.rollout_engine_count == 8
    with pytest.raises(ValueError, match="unhealthy GPU"):
        replace(layout, actor_gpus_per_node=4, context_parallel=1, rollout_excluded_gpus=[2])


def test_unified_job_overrides_only_serving_and_concurrency(tmp_path, inputs):
    from tools.slime_training_job import job_configuration

    config, *_ = inputs
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(config))
    changed = job_configuration(path, workers=28, rollout_gpus_per_engine=1,
                                rollout_excluded_gpus=(5,))
    assert changed["workers"] == 28
    assert changed["layout"]["rollout_excluded_gpus"] == [5]
    assert changed["sampling"] == config["sampling"]
    assert changed["native_arguments"] == config["native_arguments"]
    assert {k: v for k, v in changed["layout"].items()
            if k not in {"rollout_gpus_per_engine", "rollout_excluded_gpus"}} == {
                k: v for k, v in config["layout"].items() if k != "rollout_gpus_per_engine"}
    assert json.loads(path.read_text()) == config


def test_unified_job_can_reduce_training_without_changing_rollout_or_recipe(tmp_path, inputs):
    from behave.train.slime_rollout import validate_execution_change
    from tools.slime_training_job import job_configuration

    config, *_ = inputs
    config = {**config, "layout": {**config["layout"], "rollout_gpus_per_engine": 1,
        "rollout_excluded_gpus": [7]}}
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(config))
    changed = job_configuration(path, actor_gpus_per_node=4, context_parallel=1)
    record = validate_execution_change(config, changed)
    assert record["after"]["layout"]["actor_gpus_per_node"] == 4
    assert record["after"]["layout"]["context_parallel"] == 1
    assert changed["sampling"] == config["sampling"]
    assert changed["native_arguments"] == config["native_arguments"]
    assert changed["workers"] == config["workers"]
    with pytest.raises(ValueError, match="training layout"):
        validate_execution_change(config, {**changed, "layout": {**changed["layout"],
            "actor_gpus_per_node": 8}})
