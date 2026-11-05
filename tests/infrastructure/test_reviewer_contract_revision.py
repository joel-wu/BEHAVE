"""Fast binding repair keeps authors' code, rechecks, publishes and resumes."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted
from test_judge import real_task
from test_synthesis import reader_replies
from test_synthesis_source_review import package_and_policy, prepared, review_reply

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data import synthesis as synthesis_module
from behave.data.admission import admit_package
from behave.data.contract import validate_binding_revision
from behave.data.drafts import load_draft
from behave.data.process_evidence import synthesis_billing
from behave.data.publication import publish_release
from behave.data.published import load_published_task
from behave.data.source import SourceReview
from behave.data.synthesis import synthesize
from behave.task_execution.comparison import ComparisonInputs
from behave.task_execution.judgment import ComponentJudgment


def fixture(tmp_path, monkeypatch, *, revisions=1, real=False):
    options, first, _ = prepared(tmp_path, monkeypatch, revisions=revisions, real=real)
    task, _, _ = real_task(tmp_path / "valid-contract")
    good = task.contract.data
    bad = good.replace(b'"in_valid"', b'"input_valid"')
    first.insert(-1, shell_call("write", {"path": "contract.json", "content": bad.decode()}, "bad-binding"))
    seen = []

    def compare(inputs, **kwargs):
        seen.append(inputs)
        assert inputs.spec is not None
        return (ComponentJudgment(1, "matched") if inputs.contract.data == good else
                ComponentJudgment(None, "admission-control-pin", "reference"))

    if not real:
        monkeypatch.setattr(checks, "compare_sources", compare)
    return options, first, good, bad, seen


def repair_script(content):
    return [shell_call("write", {"path": "fixed_contract.json", "content": content.decode()}),
            review_reply(revised_contract="fixed_contract.json")]


def final_review(options):
    event = options["run"].journal("revisions", "r0", "source_review").load()[-1]
    ref = BlobRef.from_dict(thaw_json(event.payload)["final_action"]["source_review"])
    return SourceReview.from_bytes(options["run"].objects.read(ref))


def test_repair_reuses_pair_and_publishes_new_contract_without_a_new_revision(tmp_path, monkeypatch):
    options, first, good, bad, seen = fixture(tmp_path, monkeypatch)
    author, reader = Scripted(*first), Scripted(*reader_replies())
    result = synthesize(**options, author_backend=author, reader_backend=reader,
                        source_reviewer_backend=Scripted(*repair_script(good)))
    assert result.status == "review_ready"
    assert len(result.drafts) == 1  # Author history is not relabelled as a new author call.
    assert [p.role for p in result.phases] == ["author", "reader", "check", "source_review"]
    assert len(author.requests) == len(first) and len(reader.requests) == 2
    assert len(seen) == 2 and seen[0].source == seen[1].source
    assert seen[0].reference_behavior == seen[1].reference_behavior
    report = final_review(options)
    assert SourceReview.from_bytes(report.to_bytes()) == report
    store = options["run"].objects
    old = load_draft(store, result.drafts[-1])
    revised = load_draft(store, report.contract_revision.draft)
    assert old.file("contract.json").data == bad
    assert revised.file("contract.json").data == good
    assert revised.record.parent == result.drafts[-1]
    assert revised.record.task.reference_behavior == old.record.task.reference_behavior
    package, policy = package_and_policy(options)
    for verify in (True, False):
        admission = admit_package(package=package, policy=policy, reader=store, verify_history=verify)
        assert admission.disposition.outcome == "include"
        assert admission.task == revised.record.task
    destination = JournalDirectory(tmp_path / "release")
    published = publish_release(release_id="bindings-fixed", policy=policy, packages=(package,),
                                reader=store, destination=destination)
    task = load_published_task(destination.objects, published.release, "identity")
    assert destination.objects.read(task.manifest.contract.content) == good
    assert task.judge.contract.data == good
    head, closure = options["run"].seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    assert synthesis_billing(exported, head) == synthesis_billing(store, head)
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted()) == result
    assert len(seen) == 2


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
def test_real_sim_repair_from_declaration_failure_to_published_pair(tmp_path, monkeypatch):
    options, first, good, _, _ = fixture(tmp_path, monkeypatch, real=True)
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(*repair_script(good)))
    assert result.status == "review_ready", (result.status, result.code)
    report = final_review(options)
    assert report.contract_revision.comparison.result.score == 1
    package, policy = package_and_policy(options)
    admission = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admission.disposition.outcome == "include"
    assert options["run"].objects.read(admission.task.contract.content) == good


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
def test_process_reviewer_repair_exports_and_recovers(tmp_path, monkeypatch):
    from test_synthesis_process import fixed

    options, first, good, _, _ = fixture(tmp_path, monkeypatch, real=True)
    fixed(options, [first], [reader_replies()], [repair_script(good)])
    result = synthesize(**options)
    assert result.status == "review_ready", (result.status, result.code)
    assert all(p.process is not None for p in result.phases if p.role != "check")
    store = options["run"].objects
    package, policy = package_and_policy(options)
    destination = JournalDirectory(tmp_path / "process-release")
    published = publish_release(release_id="process-bindings-fixed", policy=policy,
        packages=(package,), reader=store, destination=destination)
    task = load_published_task(destination.objects, published.release, "identity")
    assert task.judge.contract.data == good
    head, _ = options["run"].seal("synthesis")
    assert synthesis_billing(destination.objects, head) == synthesis_billing(store, head)
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("recovery must not rerun"))
    assert synthesize(**options) == result


def test_failed_repair_returns_to_same_reviewer_with_named_feedback(tmp_path, monkeypatch):
    options, first, good, bad, seen = fixture(tmp_path, monkeypatch)
    reviewer = Scripted(*repair_script(bad), *repair_script(good))
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(seen) == 3
    assert len(result.drafts) == 1 and len(reviewer.requests) == 4
    response = thaw_json(reviewer.requests[2].messages[-2])["content"]
    assert response["error"] == "contract-repair-check-failed"
    assert response["comparison"]["report"].endswith("report.md")


def test_optional_rtl_findings_can_refer_to_the_binding_corrected_draft(tmp_path, monkeypatch):
    from behave.data.rtl import RtlCandidate
    from behave.data.source import rtl_review_files

    options, first, good, _, _ = fixture(tmp_path, monkeypatch)
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(*repair_script(good)))
    store = options["run"].objects
    revised = final_review(options).contract_revision.draft
    candidate = RtlCandidate(revised, None, load_draft(store, revised).record.workspace,
                             findings=("Explain a timing example.",))
    files = rtl_review_files(store, result.drafts[-1], store.put(candidate.to_bytes()))
    assert {f.reference.path: f.data for f in files}["review/rtl_contract.json"] == good


def test_completed_reviewer_repair_survives_coordinator_interruption(tmp_path, monkeypatch):
    options, first, good, _, seen = fixture(tmp_path, monkeypatch)
    original = synthesis_module.run_source_review

    def interrupted(**kwargs):
        original(**kwargs)
        raise RuntimeError("fixture stopped after reviewer saved its repaired contract")

    with monkeypatch.context() as stopped:
        stopped.setattr(synthesis_module, "run_source_review", interrupted)
        with pytest.raises(RuntimeError, match="fixture stopped"):
            synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                       source_reviewer_backend=Scripted(*repair_script(good)))
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=Scripted())
    assert result.status == "review_ready" and len(seen) == 2
    package, policy = package_and_policy(options)
    admission = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admission.disposition.outcome == "include"
    assert options["run"].objects.read(admission.task.contract.content) == good


@pytest.mark.parametrize("mutation", ["drop_output", "width", "signed", "clock_edge", "reset", "input_rule"])
def test_semantic_contract_changes_are_not_fast_binding_repairs(tmp_path, mutation):
    task, _, _ = real_task(tmp_path)
    image = parse_json(task.contract.data)
    if mutation == "drop_output":
        image["endpoints"].pop()
    elif mutation in {"width", "signed"}:
        image["endpoints"][0]["fields"][0]["type"][mutation] = 9 if mutation == "width" else True
    elif mutation == "clock_edge":
        image["clocks"][0]["edge"] = "falling"
    elif mutation == "reset":
        image["clocks"][0]["reset_active_low"] = True
    else:
        image["endpoints"][0]["answers_per_transaction"] = 1
    with pytest.raises(RecordError, match="Direct repair"):
        validate_binding_revision(task.contract.data, canonical_json(image))


def test_comparison_spec_is_optional_and_roundtrips_in_worker_transport(tmp_path, monkeypatch):
    options, first, good, _, seen = fixture(tmp_path, monkeypatch)
    synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
               source_reviewer_backend=Scripted(*repair_script(good)))
    store = options["run"].objects
    inputs = seen[-1]
    for selected in (inputs, replace(inputs, spec=None)):
        selected.stage(store)
        assert ComparisonInputs.from_dict(selected.as_dict(), store) == selected
    assert "spec" not in replace(inputs, spec=None).as_dict()


@pytest.mark.parametrize("consistent", [False, True])
def test_automatic_worker_checks_declaration_before_behavior(tmp_path, monkeypatch, consistent):
    from behave.task_execution import task_declaration
    from behave.task_execution.comparison import ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    options, first, good, _, seen = fixture(tmp_path, monkeypatch)
    synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
               source_reviewer_backend=Scripted(*repair_script(good)))
    order = []

    def declared(spec, contract, **kwargs):
        order.append("declaration")
        assert spec == seen[-1].spec.data and contract == good
        return (ComponentJudgment(1, "task-interface-consistent") if consistent else
                ComponentJudgment(None, "admission-control-pin", "reference"))

    def compare(*args, **kwargs):
        order.append("behavior")
        return ComponentJudgment(1, "matched")

    monkeypatch.setattr(task_declaration, "check_task_declaration", declared)
    monkeypatch.setattr(_SimulatorService, "_evaluate", compare)
    result = _SimulatorService().evaluate(ComparisonRequest(seen[-1], tmp_path / "worker"), options["run"].objects)
    assert order == (["declaration", "behavior"] if consistent else ["declaration"])
    assert result.score == (1 if consistent else None)
