"""Small Syn fixture shared by composition/handoff tests and validation snapshots."""

from behave.improve.study_plan import runtime_for


def runtime(configuration):
    # Not an experiment default: use the maintained assembler without loading
    # a site's historical pilot JSON, archive paths or machine settings.
    return runtime_for({
        "round_id": "fixture-syn", "cpus": 4, "task_revisions": 3,
        "acquired_updates": 1, "raw_updates": 1,
        "raw_encoding": {"implementation": "tokenizers/fixture", "bos_id": 1,
                         "eos_id": 2, "context_tokens": 32},
        "task_configuration": {}, "raw_configuration": {},
    }, {**configuration, "workers": 64, "judge_workers": 8})
