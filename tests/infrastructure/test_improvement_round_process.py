"""Whole-round integration with real isolated roles and public Sim checks.

Model replies, diagnosis scores and native updates are explicit fixtures. This
lane verifies orchestration, not model quality, GPU capacity or learned weights.
"""

import sys
from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call, written_content
from test_attempts import call
from test_improvement_acquisition import material
from test_improvement_learning_round import worker
from test_improvement_round import frozen_acquisition, prepared
from test_role_process import ROOT, Unavailable
from test_scope_preparation import review as scope_review
from test_slime_training_plan import inputs
from test_synthesis import reader_replies, setup
from test_synthesis_source_review import read_comparison, review_reply

from behave.agent.attempt import AttemptSpec
from behave.agent.model import Charge
from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import load_scope
from behave.data.published import ReleaseManifest, load_published_task
from behave.data.release_records import ReleasePolicy, ScopeReviewPolicy
from behave.data.role import RoleJob
from behave.data.role_policy import ProcessRoles, RolePolicy
from behave.data.scope import ScopeConfiguration
from behave.data.supervisor import ConstructionConfiguration
from behave.data.synthesis import SynthesisConfiguration
from behave.data.synthesis_records import SourceReviewPlan
from behave.improve import learning_round
from behave.improve.acquisition import AcquisitionConfiguration
from behave.improve.acquisition_records import AcquisitionInputs
from behave.improve.learning_round import LearningParent
from behave.improve.library import LibraryIndex
from behave.improve.materials import load_materials
from behave.improve.round import _bind_construction, run_round
from behave.improve.round_data import load_round_data
from behave.improve.round_evidence import load_round
from behave.isolation.model import ResourceLimits
from behave.train.plan import TrainingManifest
from behave.train.trainer import NativeAnchor
from behave.train.training_data import RawCorpus, RawSegment


def configure(options, tmp_path, monkeypatch, *, direct=False, repair_one=False):
    objects = options["reader"]
    task_options, author, _ = setup(tmp_path / "build-template", real=True)
    for path in task_options["reader"].directory.iterdir():
        objects.put(path.read_bytes())
    old_scope, _, _ = load_scope(objects, task_options["plan"].scope)
    index = LibraryIndex(objects, options["library"])
    identifier = next(iter(index.units))
    unit = index.units[identifier]
    source_path = "source/" + unit.content.path
    contract = written_content(author[1])
    scope_args = {"intent": old_scope.intent, "capabilities": list(old_scope.capabilities),
                  "adaptations": list(old_scope.adaptations), "contract": "contract.json"}

    def cited(reply):
        args = thaw_json(reply.calls[0].arguments)
        for assessment in args["assessments"]:
            for citation in assessment["citations"]:
                if citation["path"] == "source/original.py":
                    citation["path"] = source_path
        return call(reply.calls[0].name, args)

    scripts = {
        "submit_materials": [
            call("run_command", {"command": "cat brief.md"}),
            call("run_command", {"command": f"cat library/source/{identifier}.txt"}),
            call("submit_materials", material(identifier))],
        "submit_scope": [shell_call('write', {"path": "contract.json", "content": contract}),
                          call("submit_scope", scope_args)],
        "submit_scope_review": [cited(scope_review())],
        "submit_draft": [reply for i, reply in enumerate(author) if i != 1],
        "submit_reconstruction": reader_replies(),
        "submit_source_review": [read_comparison(), cited(review_reply())],
    }
    if direct:
        proposals = material(identifier)
        supporting = next(key for key in index.units if key != identifier)
        first = {**proposals["materials"][0], "proposal_id": "identity-main", "objective": "Preserve identity",
                 "supporting_source_ids": [supporting]}
        second = {**first, "proposal_id": "identity-variant", "objective": "Preserve identity with explicit bit boundaries",
                  "supporting_source_ids": []}
        scripts["submit_materials"][-1] = call("submit_materials", {**proposals, "materials": [first, second]})
        scripts["submit_draft"] = author
        # A scope call would have no scripted reply, failing the test.
        scripts.pop("submit_scope")
        scripts.pop("submit_scope_review")
    scripted = {name: [replace(reply, charge=Charge("0.01", "USD", "fixture")).as_dict()
                      for reply in replies] for name, replies in scripts.items()}
    program = f'''
import sys
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
scripts = parse_json({canonical_json(scripted)!r})
roles = [tool['name'] for tool in request.tools if tool['name'] in scripts]
assert len(roles) == 1, roles
reply = ModelReply.from_dict(scripts[roles[0]][request.turn], raw=b'round-fixture')
if {repair_one!r} and roles[0] == 'submit_source_review' and request.turn == 1 and request.call_id.startswith('round-1-a0-p1:r0:'):
    reply = ModelReply.from_dict(parse_json({canonical_json(cited(review_reply('unclear', builder_feedback='Clarify reset.')).as_dict())!r}), raw=b'round-fixture')
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    model = ModelCommand((sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program), "round-fixture/1")
    runtime = options["runtime"]
    policy = {"transport": model.identity, "checkpoint": options["base_checkpoint"].as_dict()}
    role = RolePolicy(model, shell_limits=ResourceLimits(wall_seconds=60, processes=32))
    guide = objects.put(b"Fixture review: preserve the source's identity operation and the frozen interface.")
    synthesis = replace(task_options["plan"], processes=ProcessRoles(role, role, role),
        max_revisions=1 if repair_one else task_options["plan"].max_revisions,
        author=replace(task_options["plan"].author, model_policy=policy),
        reader=replace(task_options["plan"].reader, model_policy=policy),
        source_review=SourceReviewPlan(guide, AttemptSpec("review", "source_reviewer", policy)))
    scope = replace(runtime.construction.scope.plan,
        author=replace(runtime.construction.scope.plan.author,
                       inputs=replace(runtime.construction.scope.plan.author.inputs, guidance=guide),
                       shell_limits=role.shell_limits),
        reviewer_policy=replace(runtime.construction.scope.plan.reviewer_policy, shell_limits=role.shell_limits))
    construction = ConstructionConfiguration("round-template", SynthesisConfiguration(synthesis),
                                            None if direct else ScopeConfiguration(scope))
    release = objects.put(ReleasePolicy("fixture", "acquired-training", guide, synthesis.judge_policy, policy,
        False, scope_review=None if direct else ScopeReviewPolicy(guide, policy)).to_bytes())
    acquire = RoleJob(AcquisitionInputs(options["library"], BlobRef.of(b"bound by round"), guide),
                      AttemptSpec("acquire", "source_acquirer", policy), model,
                      shell_limits=ResourceLimits(wall_seconds=20, processes=8))
    raw_config = {**runtime.raw.configuration,
        "layout": {**runtime.raw.configuration["layout"], "rollout_gpus": 0, "colocate": False},
        "sampler": {"sequences_per_update": 1, "order": "cycle", "seed": "fixture"},
        "native_arguments": ["--bf16", "--optimizer", "adam", "--lr", "1e-6"]}
    options["runtime"] = replace(runtime, model=model, model_policy=policy, construction=construction,
        release_policy=release, acquisitions=(AcquisitionConfiguration(acquire),),
        acquired_updates=1, raw_updates=1, raw=replace(runtime.raw, configuration=raw_config))
    # The public source/execution chain is real. Tokenization and native worker
    # updates are separate named fixtures, so this test starts no trainer.
    monkeypatch.setattr(learning_round, "tokenize_materials", lambda reader, ref, encoding:
        RawCorpus(ref, encoding, tuple(RawSegment(file, 0, (1, 3, 2))
                                      for file in load_materials(reader, ref).documents)))
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus",
                        lambda reader, ref: RawCorpus.from_bytes(read_verified(reader, ref)))


def test_every_construction_role_uses_the_runtime_model_and_keeps_its_budget(tmp_path, monkeypatch, publication, inputs):
    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch)
    runtime = options["runtime"]
    configured = _bind_construction(runtime.construction, runtime.model, runtime.model_policy)
    plan = configured.synthesis.plan
    assert all(role.model == runtime.model for role in
               (plan.processes.author, plan.processes.reader, plan.processes.source_review,
                configured.scope.plan.author, configured.scope.plan.reviewer_policy))
    assert all(dict(attempt.model_policy) == runtime.model_policy for attempt in
               (plan.author, plan.reader, plan.source_review.reviewer,
                configured.scope.plan.author.attempt, configured.scope.plan.reviewer))
    assert plan.max_revisions == runtime.construction.synthesis.plan.max_revisions
    assert configured.max_reopens == runtime.construction.max_reopens


@pytest.mark.isolation
@pytest.mark.parametrize("direct,repair_one", [(False, False), (True, False), (True, True)])
def test_parallel_generation_only_admits_without_training_and_cold_replays(
        tmp_path, monkeypatch, publication, inputs, direct, repair_one):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch, direct=direct, repair_one=repair_one)
    options["runtime"] = replace(options["runtime"], workers=2, proposals_per_source=None if direct else 2,
                                 generation_only=True)
    from behave.improve import round as flow

    monkeypatch.setattr(flow, "learn_round", lambda **kwargs: pytest.fail("generation started learning"))
    result = run_round(**options)
    assert result["status"] == "completed" and result["learning"] is None
    assert result["parents"] == {name: parent.record() for name, parent in options["parents"].items()}
    run = options["run"]
    data = load_round_data(run.objects, BlobRef.from_dict(result["data"]))
    assert len(ReleaseManifest.from_bytes(read_verified(run.objects, data.release)).tasks) == 2 - repair_one
    pool = TrainingManifest.from_bytes(read_verified(run.objects, BlobRef.from_dict(result["training_manifest"])))
    seeds = TrainingManifest.from_bytes(read_verified(run.objects, options["training_manifest"]))
    assert len(pool.tasks) == len(seeds.tasks) + 2 - repair_one
    assert dict(seeds.tasks).items() <= dict(pool.tasks).items()
    stages = [event.payload["name"] for event in run.journal("round").load(verify_artifacts=False)
              if event.kind == "round_stage"]
    assert len([name for name in stages if name.startswith("construction:")]) == 2
    if direct:
        assert not any(name.startswith("scope:") for name in stages)
        from behave.improve.materials import construction_proposals
        proposals = construction_proposals(run.objects, data.materials)
        assert len(proposals) == 2
        assert b'identity-main' in proposals["a0-p0"] and b'identity-variant' in proposals["a0-p1"]
        assert len(load_materials(run.objects, data.materials).documents) == 2
    assert "learning" not in stages and not (run.directory / "learning").exists()
    assert run_round(**{**options, "reader": Unavailable()}) == result
    assert calls == ["diagnosis", "release"]
    head, closure = run.seal("round")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round(exported, head) == result

    if direct:
        events = run.journal("round").load(verify_artifacts=False)
        saved = {event.payload["name"].removeprefix("construction:"): BlobRef.from_dict(thaw_json(event.payload)["head"])
                 for event in events if event.kind == "round_stage"
                 and event.payload["name"].startswith("construction:")}
        boundary = frozen_acquisition(options)
        constructed, original_construct = [], flow.construct

        def pending_only(**kwargs):
            assert repair_one and kwargs["plan"].initial.task_id == "round-1-a0-p1"
            assert kwargs["continuation_from"] is not None
            constructed.append(kwargs["plan"].initial.task_id)
            return original_construct(**kwargs)

        monkeypatch.setattr(flow, "construct", pending_only)
        if repair_one:
            construction = options["runtime"].construction
            construction = replace(construction, synthesis=replace(construction.synthesis,
                plan=replace(construction.synthesis.plan, max_revisions=2)))
            options["runtime"] = replace(options["runtime"], construction=construction)
        reused = JournalDirectory(tmp_path / "retained")
        continued = run_round(**{**options, "reader": run.objects, "run": reused},
                              acquisition_from=boundary, construction_from=saved)
        assert continued["status"] == "completed" and calls == ["diagnosis", "release", "release"]
        recorded = {event.payload["name"].removeprefix("construction:"): BlobRef.from_dict(thaw_json(event.payload)["head"])
                    for event in reused.journal("round").load(verify_artifacts=False)
                    if event.kind == "round_stage" and event.payload["name"].startswith("construction:")}
        if repair_one:
            assert constructed == ["round-1-a0-p1"]
            assert recorded["round-1-a0-p0"] == saved["round-1-a0-p0"]
            assert recorded["round-1-a0-p1"] != saved["round-1-a0-p1"]
            later = load_round_data(reused.objects, BlobRef.from_dict(continued["data"]))
            assert len(ReleaseManifest.from_bytes(read_verified(reused.objects, later.release)).tasks) == 2
        else:
            assert recorded == saved and constructed == []
        continued_head, refs = reused.seal("round")
        exported.publish(reused.objects, refs)
        assert load_round(exported, continued_head) == continued


@pytest.mark.isolation
@pytest.mark.parametrize("cleanup_pending", [False, True])
def test_target_recovery_keeps_admitted_tasks_with_terminal_unfinished_results(
        tmp_path, monkeypatch, publication, inputs, cleanup_pending):
    from behave.improve import round as flow

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch, direct=True)
    options["runtime"] = replace(options["runtime"], generation_only=True, workers=1,
        generation_target={"admitted_tasks": 1, "max_batches": 1})
    original = flow.run_stage_tasks
    stopping = True

    def collect(items, operation, **kwargs):
        items = tuple(items)
        if "should_start" in kwargs and not stopping:
            recorded = {event.payload["name"] for event in kwargs["tape"].events
                        if event.kind == "round_stage"}
            surplus = [item for item in items
                       if item is not None and not any(
                           name.endswith(f"-{item[0]}") for name in recorded
                           if name.startswith("construction:"))]
            assert surplus, "fixture must retain an unstarted surplus proposal"
            assert all(not kwargs["should_start"](item) for item in surplus)
        construction_rows = []
        on_stage = kwargs.get("on_stage", lambda row: None)

        def committed(row):
            if row["name"].startswith("construction:"):
                construction_rows.append(row)
            on_stage(row)

        # Observe the durable phase, not a wrapper function's Python name.
        # The dispatch can also contain the independent memory worker.
        outcomes = original(items, operation, **{**kwargs, "on_stage": committed})
        if not construction_rows:
            return outcomes
        if stopping:
            assert any(row["outcome"].get("package") for row in construction_rows)
            raise InterruptedError("after saved construction, before target finalization")
        # Ordinary author non-submission is a terminal construction result,
        # not the coordinator's {status: unfinished, stage: ...} interruption.
        pending = {"status": "unfinished", "code": "author-stopped", "package": None}
        extra = ({"status": "unfinished", "stage": {"name": "construction:cleanup",
                  "outcome": {"status": "cleanup_unconfirmed"}}},) if cleanup_pending else ()
        return (*outcomes, pending, *extra)

    monkeypatch.setattr(flow, "run_stage_tasks", collect)
    with pytest.raises(InterruptedError, match="before target finalization"):
        run_round(**options)
    stopping = False
    monkeypatch.setattr(flow, "construct", lambda **kw: pytest.fail("reconstructed saved task"))
    result = run_round(**options)
    if cleanup_pending:
        assert result["status"] == "unfinished"
    else:
        assert result["status"] == "completed"
        assert result["generation"]["admitted"] == 1
        assert result["generation"]["target_met"]
        assert run_round(**options) == result
        assert calls == ["diagnosis", "release"]


@pytest.mark.isolation
@pytest.mark.parametrize("target,budget,interrupt", [(3, 3, False), (20, 1, False), (3, 3, True)])
def test_target_generation_replenishes_without_new_rollouts_or_serving_restart(
        tmp_path, monkeypatch, publication, inputs, target, budget, interrupt):
    from behave.improve import generation_batch
    from behave.improve import round as flow

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch, direct=True)
    options["runtime"] = replace(options["runtime"], generation_only=True, workers=2,
        generation_target={"admitted_tasks": target, "max_batches": budget})
    monkeypatch.setattr(flow, "learn_round", lambda **kw: pytest.fail("generation started optimizer"))
    if interrupt:
        details = generation_batch._batch_details
        def stop_after_committed_child(*args):
            raise InterruptedError("parent stopped after committed generation")
        monkeypatch.setattr(generation_batch, "_batch_details", stop_after_committed_child)
        with pytest.raises(InterruptedError, match="parent stopped"):
            run_round(**options)
        assert calls == ["diagnosis"]
        monkeypatch.setattr(generation_batch, "_batch_details", details)
    result = run_round(**options)
    count = 2 if budget == 1 else 3
    assert result["status"] == "completed" and result["learning"] is None
    assert result["generation"]["admitted"] == count
    assert len(result["generation"]["batches"]) == (1 if budget == 1 else 2)
    assert result["generation"]["target_met"] is (count >= target)
    assert result["generation"]["stop_reason"] == ("target_met" if count >= target else "batch_budget_exhausted")
    assert calls == ["diagnosis", "release"]
    run = options["run"]
    pool = TrainingManifest.from_bytes(read_verified(run.objects, BlobRef.from_dict(result["training_manifest"])))
    assert len(pool.tasks) == count + 1
    assert run_round(**{**options, "reader": Unavailable()}) == result
    head, closure = run.seal("round")
    exported = DirectoryStore(tmp_path / "target-export")
    exported.publish_owned(run.objects, closure)
    assert load_round(exported, head) == result
    assert calls == ["diagnosis", "release"]


@pytest.mark.isolation
def test_two_nonempty_rounds_construct_admit_learn_and_export(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch)
    native_calls = worker(monkeypatch)
    first = run_round(**options)
    assert first["status"] == "completed" and len(native_calls) == 2, first
    objects = options["run"].objects
    data = load_round_data(objects, BlobRef.from_dict(first["data"]))
    assert len(ReleaseManifest.from_bytes(read_verified(objects, data.release)).tasks) == 1
    assert len(load_materials(objects, data.materials).documents) == 1
    assert run_round(**{**options, "reader": Unavailable()}) == first
    assert calls == ["diagnosis", "release"] and len(native_calls) == 2

    parents = {route: LearningParent.from_record(value) for route, value in first["parents"].items()}
    first_pool = TrainingManifest.from_bytes(read_verified(objects, BlobRef.from_dict(first["training_manifest"])))
    # This low-level fixture supplies its own diagnosis plan. Bind the next
    # plan's public tasks to the new cumulative release, just like its caller.
    diagnosis = replace(options["runtime"].diagnosis, jobs=tuple(
        replace(job, task=load_published_task(objects, first_pool.release, job.task.task_id).public)
        for job in options["runtime"].diagnosis.jobs))
    next_runtime = replace(options["runtime"], model_policy={**options["runtime"].model_policy,
                                                           "checkpoint": parents["task"].checkpoint.as_dict()},
                           diagnosis=diagnosis)
    next_run = JournalDirectory(tmp_path / "round-2")
    second = run_round(**{**options, "round_id": "round-2", "parents": parents, "runtime": next_runtime,
                          "training_manifest": BlobRef.from_dict(first["training_manifest"]),
                          "reader": objects, "run": next_run})
    assert second["status"] == "completed" and len(native_calls) == 4
    second_pool = TrainingManifest.from_bytes(read_verified(next_run.objects,
        BlobRef.from_dict(second["training_manifest"])))
    assert dict(first_pool.tasks).items() <= dict(second_pool.tasks).items()
    assert len(second_pool.tasks) == len(first_pool.tasks) + 1
    for route in ("task", "raw"):
        anchor = NativeAnchor.load(next_run.objects, BlobRef.from_dict(second["parents"][route]["checkpoint"]))
        assert anchor.route == route and anchor.step == 1
    head, closure = next_run.seal("round")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(next_run.objects, closure)
    assert load_round(exported, head) == second


@pytest.mark.isolation
def test_target_batches_use_one_existing_learning_handoff_after_release(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch, direct=True)
    options["runtime"] = replace(options["runtime"], workers=2,
        generation_target={"admitted_tasks": 3, "max_batches": 2})
    native_calls = worker(monkeypatch)
    original = learning_round.run_learning
    def after_release(**kwargs):
        assert calls == ["diagnosis", "release"]
        return original(**kwargs)
    monkeypatch.setattr(learning_round, "run_learning", after_release)
    result = run_round(**options)
    assert result["status"] == "completed" and result["generation"]["admitted"] == 3, result
    assert len(native_calls) == 2  # One Task pass and one Raw pass, not one per batch.
    assert calls == ["diagnosis", "release"]
    assert run_round(**{**options, "reader": Unavailable()}) == result
    assert len(native_calls) == 2
    head, closure = options["run"].seal("round")
    exported = DirectoryStore(tmp_path / "learned-target-export")
    exported.publish_owned(options["run"].objects, closure)
    assert load_round(exported, head) == result


@pytest.mark.isolation
def test_disconnected_parent_reuses_completed_parallel_children(tmp_path, monkeypatch, publication, inputs):
    from behave.improve import round as flow

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch)
    options["runtime"] = replace(options["runtime"], workers=2, proposals_per_source=2,
                                 generation_only=True)
    prepare = flow.prepare_scope

    def disconnect(**kwargs):
        result = prepare(**kwargs)
        if kwargs["run"].directory.name.endswith("-0"):
            raise InterruptedError("parent disconnected after scope was saved")
        return result

    monkeypatch.setattr(flow, "prepare_scope", disconnect)
    monkeypatch.setattr(flow, "learn_round", lambda **kwargs: pytest.fail("generation started learning"))
    with pytest.raises(InterruptedError, match="parent disconnected"):
        run_round(**options)

    run = options["run"]
    retained = {}
    for family, journal in (("scope", "scope"), ("construction", "construction")):
        for directory in (run.directory / family).iterdir():
            retained[(family, directory.name)] = JournalDirectory(directory).seal(journal)[0]
    assert sum(family == "scope" for family, _ in retained) == 2
    assert sum(family == "construction" for family, _ in retained) == 1

    monkeypatch.setattr(flow, "prepare_scope", prepare)
    result = run_round(**options)
    assert result["status"] == "completed" and result["learning"] is None
    for (family, name), head in retained.items():
        assert JournalDirectory(run.directory / family / name).seal(family)[0] == head
    data = load_round_data(run.objects, BlobRef.from_dict(result["data"]))
    assert len(ReleaseManifest.from_bytes(read_verified(run.objects, data.release)).tasks) == 2
    assert calls == ["diagnosis", "release"]
    head, closure = run.seal("round")
    exported = DirectoryStore(tmp_path / "resumed-export")
    exported.publish(run.objects, closure)
    assert load_round(exported, head) == result


@pytest.mark.isolation
def test_new_construction_snapshot_reuses_acquired_source_and_both_learning_paths(
        tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    configure(options, tmp_path, monkeypatch)
    native_calls = worker(monkeypatch)
    first = run_round(**options)
    prior = options["run"]
    material_head = frozen_acquisition(options)
    scoped = next(thaw_json(event.payload) for event in prior.journal("round").load(verify_artifacts=False)
                  if event.kind == "round_stage" and event.payload["name"].startswith("scope:"))
    task_id = scoped["name"].removeprefix("scope:")
    parent = BlobRef.from_dict(scoped["outcome"]["scopes"][-1])
    runtime = options["runtime"]
    model = replace(runtime.model, revision="round-fixture/construction-retry")
    runtime = replace(runtime, model=model, model_policy={**runtime.model_policy, "transport": model.identity})
    from behave.improve import round as flow

    monkeypatch.setattr(flow, "run_batch", lambda **kwargs: pytest.fail("new diagnostic rollout"))
    monkeypatch.setattr(flow, "acquire_materials", lambda **kwargs: pytest.fail("new material selection"))
    retry = JournalDirectory(tmp_path / "construction-retry")
    result = run_round(**{**options, "runtime": runtime, "reader": prior.objects,
        "run": retry, "acquisition_from": material_head, "scope_parents": {task_id: parent}})
    assert result["status"] == "completed" and len(native_calls) == 4
    assert calls == ["diagnosis", "release", "release"]
    data = load_round_data(retry.objects, BlobRef.from_dict(result["data"]))
    old_data = load_round_data(prior.objects, BlobRef.from_dict(first["data"]))
    assert data.materials == old_data.materials
    assert len(ReleaseManifest.from_bytes(read_verified(retry.objects, data.release)).tasks) == 1
    assert (retry.directory / "scope").is_dir() and not (retry.directory / "diagnosis").exists()
    reviewed = next(thaw_json(event.payload) for event in retry.journal("round").load(verify_artifacts=False)
                    if event.kind == "round_stage" and event.payload["name"] == scoped["name"])
    proposal = BlobRef.from_dict(reviewed["outcome"]["scopes"][-1])
    assert proposal != parent and reviewed["head"] != scoped["head"]
    assert load_scope(retry.objects, proposal)[0].parent == parent
    for route in ("task", "raw"):
        anchor = NativeAnchor.load(retry.objects, BlobRef.from_dict(result["parents"][route]["checkpoint"]))
        assert anchor.route == route and anchor.step == 0
    head, closure = retry.seal("round")
    exported = DirectoryStore(tmp_path / "retry-export")
    exported.publish(retry.objects, closure)
    assert load_round(exported, head) == result
