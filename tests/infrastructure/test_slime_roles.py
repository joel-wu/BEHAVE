"""Native role paths and complete-state recovery, without importing Slime."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.core.records import parse_json
from behave.train.slime_checkpoint import checkpoint_files
from behave.train.slime_launch import require_checkpoint_options, role_configuration, write_role_configuration


def options(tmp_path, *, critic=True):
    return SimpleNamespace(load=str(tmp_path / "initial"), save=str(tmp_path / "saved"),
        use_critic=critic, num_critic_only_steps=0, megatron_config_path=None,
        no_save_optim=False, no_save_rng=False, no_load_optim=False, no_load_rng=False,
        use_checkpoint_opt_param_scheduler=True, override_opt_param_scheduler=False,
        finetune=False, start_rollout_id=None)


def checkpoint(root, step=1, data=b"opaque native state"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest_checkpointed_iteration.txt").write_text(str(step))
    path = root / f"iter_{step:07d}"
    path.mkdir(exist_ok=True)
    (path / "state.pt").write_bytes(data)


def test_ppo_roles_never_overwrite_actor_and_resume_both(tmp_path):
    args = options(tmp_path)
    initial = role_configuration(args)["megatron"]
    assert initial[0]["overrides"] == {}
    assert initial[1]["overrides"] == {"load": args.load, "save": args.save + "/critic"}
    checkpoint(Path(args.save))
    args.load = args.save
    with pytest.raises(FileNotFoundError):
        role_configuration(args)
    checkpoint(Path(args.save) / "critic")
    resumed = role_configuration(args)["megatron"][1]["overrides"]
    assert resumed == {"load": args.load + "/critic", "save": args.save + "/critic"}
    write_role_configuration(args, tmp_path)
    assert parse_json(Path(args.megatron_config_path).read_bytes())["megatron"][1]["overrides"] == resumed
    assert Path(args.megatron_config_path).stat().st_mode & 0o777 == 0o600


def test_critic_bytes_and_iteration_are_part_of_native_inventory(tmp_path):
    checkpoint(tmp_path)
    checkpoint(tmp_path / "critic", data=b"critic before")
    before = checkpoint_files(tmp_path, 1, critic=True)
    assert {file.path for file in before} == {"iter_0000001/state.pt", "critic/iter_0000001/state.pt"}
    checkpoint(tmp_path / "critic", data=b"critic after")
    assert before != checkpoint_files(tmp_path, 1, critic=True)
    (tmp_path / "critic/latest_checkpointed_iteration.txt").write_text("2")
    with pytest.raises(ValueError, match="round"):
        checkpoint_files(tmp_path, 1, critic=True)


def test_ppo_source_cannot_commit_or_restore_only_actor_state(tmp_path):
    from test_slime_application import configured

    source = configured(tmp_path, "ppo")
    events = ()
    for kind in ("round_started", "round_scored"):
        events += (source.journal.append(events, kind, {"round": 0, "binding": source.identity.as_dict()}),)
    root = Path(source.args.save)
    checkpoint(root, 0)
    with pytest.raises(FileNotFoundError):
        source.save(0)
    assert source.journal.load()[-1].kind == "round_scored"
    checkpoint(root / "critic", 0)
    source.save(0)
    source.load(0)
    checkpoint(root / "critic", 0, b"silently replaced critic")
    with pytest.raises(ValueError, match="file inventory differs"):
        source.load(0)


@pytest.mark.parametrize("name", ["no_save_optim", "no_save_rng", "no_load_optim", "no_load_rng", "finetune"])
def test_cold_continuation_cannot_drop_optimizer_or_rng(tmp_path, name):
    args = options(tmp_path)
    checkpoint(Path(args.load))
    setattr(args, name, True)
    with pytest.raises(ValueError, match=name):
        require_checkpoint_options(args)


@pytest.mark.parametrize("use,override", [(False, False), (True, True)])
def test_cold_continuation_keeps_the_saved_scheduler(tmp_path, use, override):
    args = options(tmp_path)
    checkpoint(Path(args.load))
    args.use_checkpoint_opt_param_scheduler = use
    args.override_opt_param_scheduler = override
    with pytest.raises(ValueError, match="use-checkpoint-opt-param-scheduler"):
        require_checkpoint_options(args)
    args.use_checkpoint_opt_param_scheduler, args.override_opt_param_scheduler = True, False
    require_checkpoint_options(args)


def test_initial_hf_and_ordinary_actor_do_not_need_critic(tmp_path):
    args = options(tmp_path, critic=False)
    args.no_load_optim = args.no_load_rng = args.finetune = True
    args.start_rollout_id = 0
    assert role_configuration(args) is None
    write_role_configuration(args, tmp_path)
    assert args.megatron_config_path is None


def test_native_role_overrides_and_skipped_rounds_are_not_silent(tmp_path):
    args = options(tmp_path)
    args.megatron_config_path = "/unbound.yaml"
    with pytest.raises(ValueError, match="owns"):
        role_configuration(args)
    args.megatron_config_path = None
    args.num_critic_only_steps = 1
    with pytest.raises(ValueError, match="both"):
        role_configuration(args)
    args.num_critic_only_steps = 0
    args.start_rollout_id = 4
    with pytest.raises(ValueError, match="zero"):
        require_checkpoint_options(args)
    checkpoint(Path(args.load))
    with pytest.raises(ValueError, match="next round"):
        require_checkpoint_options(args)
