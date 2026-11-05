"""Freeze Syn settings by reusing the reviewed training runtime.

Preparation only: the native trainer owns serving and calls the shared
run_round controller at acquisition boundaries. No job is launched here.
"""

from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.improve.library import import_library
from behave.train.plan import load_configuration


def load_settings(path, repository):
    """Apply repository-relative bases once, with child keys replacing parent keys."""
    layers, seen = [], set()
    path, repository = Path(path), Path(repository)
    while True:
        identity = path.resolve()
        if identity in seen:
            raise ValueError(f"cyclic base_settings inheritance: {identity}")
        seen.add(identity)
        settings = parse_json(path.read_bytes())
        base = settings.pop("base_settings", None)
        layers.append(settings)
        if base is None:
            break
        if type(base) is not str or not base:
            raise ValueError("base_settings must be a nonempty path or null")
        path = repository / base
    result = {}
    for settings in reversed(layers):
        result.update(settings)
    return result


def runtime_for(settings, configuration):
    from behave.sim import api

    runtime = {key: value for key, value in settings.items()
               if key not in {"training_configuration", "resources", "stage_workers", "study"}}
    runtime.update(
        cpu_threads=configuration["cpu_threads"], build_jobs=configuration["build_jobs"],
        workers=configuration["workers"], judge_workers=configuration["judge_workers"],
        check_workers=configuration["judge_workers"],
        role_log_bytes=defaults.LOG_RETAIN_BYTES, attempt=configuration["runtime"],
        shell_limits=api.development_limits().as_dict(), sampling=configuration["sampling"],
        output_preview_bytes=runtime.get("output_preview_bytes", defaults.SHELL_OUTPUT_PREVIEW_BYTES),
        judge_version={"command": api.COMMAND_VERSION, "authority": api.RESULT_VERSION},
        raw_configuration={**settings["raw_configuration"],
                           "layout": {**configuration["layout"], "rollout_gpus": 0}},
    )
    runtime["task_configuration"] = {
        "output_preview_bytes": runtime["output_preview_bytes"], **runtime["task_configuration"],
    }
    if runtime.get("require_recorded_feedback"):
        runtime.pop("diagnosis_tasks", None)
        runtime.pop("diagnosis_configuration", None)
    return runtime


def study_files(settings, runtime, configuration):
    """Prepare explicit stage files, not a new trainer or automatic launch loop."""
    study = settings["study"]
    for name, minimum in (("seed_updates", 1), ("acquisition_rounds", 0)):
        if type(study[name]) is not int or study[name] < minimum:
            raise ValueError(f"study.{name} must be an integer >= {minimum}")
    if study["acquisition_rounds"] and (
            type(runtime["acquired_updates"]) is not int or runtime["acquired_updates"] < 1):
        raise ValueError("acquired_updates must be a positive integer for study acquisition rounds")
    updates = study["seed_updates"]
    files = {"seed_configuration.json": {
        **configuration, **runtime["task_configuration"],
        "initial_updates": updates, "resume_updates": 0,
    }}
    stages = [{"stage": "seed_training", "configuration": "seed_configuration.json",
               "release": runtime["diagnosis_release"], "tasks_file": runtime["diagnosis_split"],
               "first_update": 0, "stop_update": updates}]
    for index in range(study["acquisition_rounds"]):
        name = f"acquisition_{index + 1:02d}.json"
        files[name] = {**runtime, "round_id": f"{runtime['round_id']}-r{index + 1:02d}"}
        stop = updates + runtime["acquired_updates"]
        stages.append({"stage": "acquisition_and_learning", "runtime": name,
                       "feedback": "completed_training_groups", "after_updates": updates,
                       "first_update": updates, "stop_update": stop})
        updates = stop
    milestones = [stage["stop_update"] for stage in stages]
    # A single native trainer owns all updates; preparation binds its acquisition
    # callback to this study below. This does not launch or validate a GPU run.
    files["native_configuration.json"] = {
        **configuration, **runtime["task_configuration"],
        "initial_updates": updates, "resume_updates": 0,
        "hf_checkpoint_updates": milestones,
    }
    checkpoints = [{"completed_updates": step, "rollout_id": step - 1,
                    "hf_relative_path": f"artifacts/plan/hf_{step - 1}",
                    "after_stage": stage["stage"],
                    **({"acquisition_round": index} if index else {})}
                   for index, (stage, step) in enumerate(zip(stages, milestones))]
    files["study.json"] = {"stages": stages, "total_task_updates": updates,
        "automatic_launch": False, "native_configuration": "native_configuration.json",
        "checkpoint_milestones": checkpoints,
        "native_checkpoint_relative_path": "artifacts/checkpoint",
        "generation_binding": {"engine_owner": "native-training",
            "entrypoint": "behave.improve.round.run_round",
            "generation_only": True, "keep_serving": True,
            "feedback": "completed_training_groups", "hf_reload_required": False,
            "native_callback_connected": True}}
    return files


def prepare(settings_path, output, *, repository, machine_profile=None):
    """Freeze a study using caller-supplied input paths; never submit a job."""
    repository = Path(repository)
    settings_path, output = Path(settings_path), Path(output)
    settings = load_settings(settings_path, repository)
    configuration = load_configuration(repository / settings["training_configuration"],
                                       machine_profile=machine_profile)
    runtime = runtime_for(settings, configuration)
    for name in ("diagnosis_release", "diagnosis_split"):
        runtime[name] = str((repository / runtime[name]).resolve())
    guides = {name: f"guides/{name}.md" for name in settings["guides"]}
    guide_contents = {guides[name]: (repository / source).read_bytes()
                      for name, source in settings["guides"].items()}
    runtime["guides"] = guides
    files = {"configuration.json": configuration, "runtime.json": runtime, "settings.json": settings}
    if "study" in settings:
        files.update(study_files(settings, runtime, configuration))
        files["native_configuration.json"]["self_improvement"] = {
            "study": str((output / "study.json").resolve())}
    output.mkdir(parents=True, exist_ok=False)

    def install(name, data):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not install_exclusive(path.parent, path.name, data):
            raise FileExistsError(path)

    for name, data in guide_contents.items():
        install(name, data)
    for name, value in files.items():
        install(name, canonical_json(value))
    root = repository / settings["resources"]
    objects = DirectoryStore(output / "library/objects")
    library = import_library(root, BlobRef.of((root / "catalog.jsonl").read_bytes()), objects)
    install("library/selection.json", canonical_json({"library": library.as_dict(), "source": str(root),
        "split": runtime["diagnosis_split"], "authored_answers_in_library": False}))
    return {"configuration": str((output / "configuration.json").resolve()),
            "runtime": str((output / "runtime.json").resolve()),
            "library": str((output / "library").resolve()), "model_started": False,
            **({"study": str((output / "study.json").resolve())} if "study" in settings else {})}
