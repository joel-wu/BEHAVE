"""Freeze PPA campaign inputs without importing a cluster controller.

One externally fenced owner prepares a campaign. Matching immutable files may
be reused after interrupted preparation; different bytes are never overwritten.
This does not resume a campaign, dispatch a model or settle a spending account.
"""

from copy import deepcopy
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.runtime.records import publish_bytes as _install
from behave.runtime.records import publish_json as publish

from .planning import validate_campaign

_OPTIONAL_INPUTS = ("judge_policy", "final_judge_policy", "final_experiment")


def validate_durable_campaign(root, plans):
    """Reject known disposable locations for paid journals and accounts.

    Operators still choose a durable mount. This does not certify an arbitrary
    filesystem, relocate accounts or read credential files.
    """
    accounts = set()
    for source in {method["provider"] for plan in plans for method in plan["methods"]}:
        spending = parse_json(Path(source).read_bytes()).get("spending")
        if spending is not None:
            accounts.add(spending["directory"])
    if not accounts:
        return
    for location in (str(root), *sorted(accounts)):
        path = Path(location).resolve()
        if any(path.is_relative_to(scratch) for scratch in
               (Path("/tmp"), Path("/var/tmp"), Path("/dev/shm"))):
            raise ValueError("paid PPA journals and spending accounts require durable shared "
                             f"storage, not scratch: {location}")


def freeze_inputs(plan, root):
    """Copy the selected task's verified closure and explicit configurations.

    The caller validates the campaign plan. Its trusted archive retains RTL,
    Behavior helpers, public files, policies and manifest metadata; agent
    visibility is unchanged. Native tool paths and credential references remain
    external; no source-store scan or credential read occurs.
    """
    from behave.core.artifacts import DirectoryReader
    from behave.core.storage import DirectoryStore
    from behave.data.published import load_published_task
    from behave.data.published.files import task_files

    from .records import release_reference

    root = Path(root)
    result = deepcopy(plan)
    reader = DirectoryReader(plan["objects"])
    release = release_reference(plan["release"])
    task = load_published_task(reader, release, plan["task"])
    refs = {release, task.public.revision, task.release.policy,
            task.manifest.run_policy, task.manifest.judge_policy}
    refs.update(file.content for file in task_files(task.manifest))
    store = DirectoryStore(root / "objects")
    store.publish(reader, tuple(refs))
    result["objects"] = str(store.directory)

    def copy(source, name):
        destination = root / "configuration" / name
        _install(destination, Path(source).read_bytes())
        return str(destination)

    for key in ("release", "experiment", "retained_pool",
                *(k for k in _OPTIONAL_INPUTS if plan.get(k))):
        result[key] = copy(plan[key], key + ".json")
    for method in result["methods"]:
        method["provider"] = copy(method["provider"], method["name"] + "-provider.json")
        method["prompt"] = copy(method["prompt"], method["name"] + "-prompt.md")
    return result


def freeze_plan(plan, root):
    """Commit the input digest inventory before publishing the runnable plan."""
    root = Path(root)
    plan = freeze_inputs(validate_campaign(plan), root)
    paths = [plan[k] for k in ("release", "experiment", "liberty", "sta", "retained_pool")]
    paths += [plan[k] for k in _OPTIONAL_INPUTS if plan.get(k)]
    paths += [m[k] for m in plan["methods"] for k in ("provider", "prompt")]
    publish(root / "inputs.json", {p: BlobRef.of(Path(p).read_bytes()).sha256 for p in paths})
    publish(root / "plan.json", plan)
    return plan
