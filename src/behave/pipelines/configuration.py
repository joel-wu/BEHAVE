"""External-method settings share ordinary Eval input preparation."""

from dataclasses import asdict, replace
from pathlib import Path

from behave.eval.followup import prepare_review as prepare_eval
from behave.runtime.records import write_json

from .provider import sampling_policy


def method_settings(name, options=None):
    from behave.pipelines.chipmate import ChipmateSettings
    from behave.pipelines.mage import MageSettings
    return {"chipmate": ChipmateSettings, "mage": MageSettings}[name](**(options or {}))


def baseline_policy(saved):
    """Keep the release's baseline parameters, selecting current Sim without search."""
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    return replace(JudgePolicy.from_bytes(saved), command_version=api.COMMAND_VERSION,
                   authority_version=api.RESULT_VERSION, search_seconds=0, analysis=None)


def prepare(output, template, method, *, tasks=None, workers=None, options=None, provider_sampling="upstream"):
    sampling_policy(provider_sampling)
    output = Path(output)
    configured = method_settings(method, options)
    prepare_eval(output, template, setting="rtl_only", tasks=tasks, workers=workers, rollout_only=True)
    write_json(Path(output) / "pipeline.json", {"method": method, "options": asdict(configured),
                                              "provider_sampling": provider_sampling})
    return {"method": method, "options": asdict(configured), "output": str(output)}
