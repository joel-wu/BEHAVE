"""Release acceptance for a consumer outside the BEHAVE package."""

from __future__ import annotations

import ast
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from behave.sim import api

ROOT = Path(__file__).resolve().parents[2]
CONSUMER = ROOT / "examples" / "public_sim_consumer.py"


def test_external_consumer_imports_only_the_public_simulator_facade():
    tree = ast.parse(CONSUMER.read_text(encoding="utf-8"), CONSUMER.name)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names
                           if alias.name == "behave"
                           or alias.name.startswith("behave."))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "behave" or module.startswith("behave."):
                imports.append((module, tuple(alias.name for alias in node.names)))

    assert imports == [("behave.sim", ("api",))]


def test_external_consumer_uses_the_public_behavior_path(tmp_path):
    consumer = runpy.run_path(str(CONSUMER))

    got = consumer["run_behavior"](tmp_path)

    assert got["command_status"] == "evaluated"
    assert got["analysis_bound"] == 1
    assert got["evaluation"]["passed"] is True
    assert got["evaluation"]["accepted"] > 0
    assert len(got["generation_providers"]) == 2
    assert got["analysis"]["verdict"] == "classified"
    assert got["analysis"]["concrete"] == got["evaluation"]["identity"]
    assert got["analysis"]["representation"].startswith(
        "behavior-position-representation/")
    # Analysis admits the first record and may leave a later record with the
    # same goal support unselected; neither record is refused or outside.
    audits = got["analysis"]["record_audits"]
    assert audits[0] == "admitted" and set(audits) <= {"admitted", "not-selected"}, audits
    assert got["record_persisted"] is True


def test_external_consumer_can_use_the_score_only_training_path(
        tmp_path, monkeypatch):
    from behave.sim import worker

    jobs = []
    run_job = worker.run_job_strict

    def observed(name, *args, **kwargs):
        jobs.append(name)
        if name == "behavior_exact_analysis":
            raise AssertionError("score-only preparation entered bounded lowering")
        return run_job(name, *args, **kwargs)

    monkeypatch.setattr(worker, "run_job_strict", observed)
    consumer = runpy.run_path(str(CONSUMER))

    got = consumer["run_behavior"](tmp_path, run_analysis=False)

    assert got["command_status"] == "evaluated"
    assert got["analysis_requested"] is False
    assert got["evaluation"]["passed"] is True
    assert got["evaluation"]["accepted"] > 0
    assert got["analysis"] is None
    assert got["record_persisted"] is True
    assert "behavior_realization" in jobs
    assert "behavior_exact_analysis" not in jobs


def test_public_behavior_mismatch_uses_the_same_score_and_analysis_path(tmp_path):
    consumer = runpy.run_path(str(CONSUMER))
    consumer["run_behavior"].__globals__["BEHAVIOR_SOURCE"] = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a ^ 1}
"""

    got = consumer["run_behavior"](tmp_path)

    assert got["command_status"] == "evaluated"
    assert got["evaluation"]["passed"] is False
    assert got["evaluation"]["accepted"] > 0
    assert len(got["generation_providers"]) == 2
    assert got["analysis"]["verdict"] == "classified"
    assert got["analysis"]["concrete"] == got["evaluation"]["identity"]
    assert got["analysis"]["record_audits"] == ["admitted"]
    assert got["record_persisted"] is True


@pytest.mark.isolation
@pytest.mark.solver
def test_public_behavior_uses_both_production_generation_directions(tmp_path):
    """B*/B-hat and B-hat/B-hat share the common scheduler and replay."""

    consumer = runpy.run_path(str(CONSUMER))
    candidate_source = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        if a != 7:
            y = a
        else:
            y = a
        return {"y": y}
"""
    reference_source = """\
class Identity:
    def reset(self):
        pass

    def process(self, in_a):
        if in_a != 7:
            out_y = in_a
        else:
            out_y = in_a
        return {"out_y": out_y}
"""
    contract = consumer["_contract"]()
    generation = consumer["_generation"]()
    path = tmp_path / "candidate_generation.py"
    path.write_text(candidate_source, encoding="utf-8")
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()},
        config={"class": "Identity"}, loader="two-sided Behavior generation test")
    realization = api.realization_of_behavior_source(
        candidate_source, contract, file=path.name)
    task = api.admit(contract, realization, reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file=path.name, timeout_s=120),
        generation, bounded_analysis=True)
    # Both candidate guidance and replay must use the source frozen by the handle,
    # never a later path replacement.
    path.write_text(
        "class Replaced:\n"
        "    def reset(self): pass\n"
        "    def process(self, a): return {'y': 0}\n",
        encoding="utf-8",
    )
    providers = handle.generation_providers(
        reference, workdir=tmp_path / "generation-contexts")

    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=providers,
        probe_budget=1,
        run_analysis=True,
        reference=reference,
        feasibility_timeout_ms=30_000,
        query_timeout_ms=30_000,
        analysis_wall_timeout_seconds=180,
    ))

    assert result.status.value == "evaluated"
    assert result.evaluation is not None and result.evaluation.passed
    assert result.analysis is not None
    assert len(result.generated.searches) == 2
    names = {search.provider for search in result.generated.searches}
    assert f"behavior/{reference.digest}->{task.realization.identity}" in names
    assert (f"behavior/candidate:{task.realization.identity}->"
            f"{task.realization.identity}") in names
    assert all(search.baseline == result.generated.baseline
               for search in result.generated.searches)
    assert all(search.generated for search in result.generated.searches)
    generated_by = {
        dict(row)["provider"]
        for rows in result.generated.provenance.values()
        for row in rows
    }
    assert generated_by == names
    assert any(
        transaction == (7,)
        for stimulus in result.generated.stimuli
        for endpoint, transactions in stimulus.queues if endpoint == "in"
        for transaction in transactions
    )


def test_public_behavior_realization_rejects_another_task_boundary():
    consumer = runpy.run_path(str(CONSUMER))
    source = """\
class M:
    def reset(self):
        pass

    def process(self, b):
        return {"y": b}
"""

    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_behavior_source(
            source,
            consumer["_contract"](),
            file="wrong_boundary.py",
        )

    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.reason.kind == "behavior-boundary-input-binding-incomplete"


def test_behavior_realization_is_source_bound_and_does_not_lower():
    consumer = runpy.run_path(str(CONSUMER))
    source = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        try:
            y = a
        except Exception:
            y = 0
        return {"y": y}
"""
    contract = consumer["_contract"]()

    first = api.realization_of_behavior_source(
        source, contract, file="stable/source.py")
    same = api.realization_of_behavior_source(
        source, contract, file="stable/source.py")
    relabelled = api.realization_of_behavior_source(
        source, contract, file="other/source.py")

    assert first.identity.startswith("behavior-source/2:")
    assert same.identity == first.identity
    assert relabelled.identity != first.identity


def test_behavior_exact_snapshot_must_reproduce_the_realized_source(tmp_path):
    consumer = runpy.run_path(str(CONSUMER))
    contract = consumer["_contract"]()
    realization = api.realization_of_behavior_source(
        consumer["BEHAVIOR_SOURCE"], contract, file="candidate.py")
    task = api.admit(contract, realization)
    changed = tmp_path / "candidate.py"
    changed.write_text(consumer["BEHAVIOR_SOURCE"].replace(
        "class M:\n",
        "class M:\n"
        "    def __init__(self):\n"
        "        raise RuntimeError('unadmitted source executed')\n\n",
    ), encoding="utf-8")

    with pytest.raises(api.Refused) as caught:
        api.prepare_behavior(
            task,
            api.BehaviorConfig(changed, file="candidate.py"),
            consumer["_generation"](),
        )

    assert caught.value.refusal.kind == \
        "behavior-public-exact-artifact-mismatch"


def test_one_clock_behavior_uses_the_transaction_step_boundary():
    consumer = runpy.run_path(str(CONSUMER))
    source = """\
class M:
    def reset(self):
        pass

    def process_clk(self, a):
        return {"y": a}
"""

    with pytest.raises(api.Refused) as caught:
        api.realization_of_behavior_source(
            source, consumer["_contract"](), file="named_single_clock.py")

    # A one-clock contract binds the transaction step, so a handler named
    # for a clock domain leaves that contract's domain without a handler.
    assert caught.value.refusal.kind == \
        "behavior-boundary-clock-domains-mismatch"


@pytest.mark.cosim
def test_external_consumer_runs_from_outside_the_checkout(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    completed = subprocess.run(
        [sys.executable, str(CONSUMER), str(outside / "run")],
        cwd=outside,
        text=True,
        capture_output=True,
        # This is the one full public-boundary test: it builds both production
        # generation directions and the post-score exact analysis for Behavior and
        # real RTL. Context reuse is a separately measured T3-17 optimization.
        timeout=15 * 60,
        check=False,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    got = json.loads(completed.stdout)
    assert got["status"] == "ready"
    assert got["behavior"]["command_status"] == "evaluated"
    assert got["behavior"]["analysis_bound"] == 1
    assert got["behavior"]["evaluation"]["passed"] is True
    assert got["behavior"]["evaluation"]["accepted"] > 0
    assert len(got["behavior"]["generation_providers"]) == 2
    assert got["behavior"]["analysis"]["verdict"] == "classified"
    assert got["behavior"]["analysis"]["concrete"] == \
        got["behavior"]["evaluation"]["identity"]
    assert got["behavior"]["record_persisted"] is True
    assert got["rtl"]["artifact"]
    assert got["rtl"]["admission"]
    assert got["rtl"]["catalogue"]
    assert got["rtl"]["command_status"] == "evaluated"
    assert got["rtl"]["analysis_bound"] == 5
    assert got["rtl"]["evaluation"]["passed"] is True
    assert got["rtl"]["evaluation"]["accepted"] > 0
    assert len(got["rtl"]["generation_providers"]) == 2
    assert len(got["rtl"]["evaluation"]["identity"]) == 64
    assert got["rtl"]["analysis"]["verdict"] == "classified"
    assert got["rtl"]["analysis"]["concrete"] == \
        got["rtl"]["evaluation"]["identity"]
    assert got["rtl"]["analysis"]["representation"].startswith(
        "rtl-product:")
    assert got["rtl"]["analysis"]["record_audits"] == ["admitted"]
    assert got["rtl"]["record_persisted"] is True
    generation = got["rtl"]["generation"]
    assert generation == {
        "horizon": 5,
        "queue_bound": 1,
        "reset_hold_cycles": 3,
        "drain": 1,
        "stall_every": None,
        "max_body_steps": 1,
    }
