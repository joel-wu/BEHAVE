"""A B/R no-result rechecks the same RTL, with unchanged semantic budgets."""

from dataclasses import replace

import pytest
from test_role_process import Unavailable
from test_rtl_construction import auxiliary, rtl_replies, setup_rtl
from test_synthesis_source_review import package_and_policy

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data import checks, synthesis as synthesis_module
from behave.data import rtl as rtl_module
from behave.data.admission import admit_package
from behave.data.continuation import load_continuation
from behave.data.process_evidence import synthesis_billing
from behave.data.rtl import construction_status
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


def unavailable():
    return ComponentJudgment(None, "fixture-runtime-unavailable", origin="verifier", retryable=True)


def continued(options, tmp_path, name):
    head, _ = options["run"].seal("synthesis")
    return ({**options, "reader": options["run"].objects,
             "plan": replace(options["plan"], run_id=name),
             "run": JournalDirectory(tmp_path / name)}, head)


def no_roles(*args, **kwargs):
    pytest.fail("a check-only continuation must not call an author or reviewer")


@pytest.mark.isolation
@pytest.mark.eda
def test_rtl_recheck_cli_public_sim_admission_export_and_cold_recovery(tmp_path, monkeypatch, capsys):
    from behave.data.__main__ import main
    from behave.data.synthesis import SynthesisConfiguration
    from behave.data.synthesis_records import SynthesisResult

    options = setup_rtl(tmp_path / "first", [rtl_replies()], real=True)
    compare = checks.compare_sources
    monkeypatch.setattr(checks, "compare_sources", lambda inputs, **kwargs:
                        unavailable() if inputs.component == "rtl" else compare(inputs, **kwargs))
    first = synthesize(**options)
    built, _, _ = auxiliary(options, first)
    assert built.status == "check_unavailable"
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "pending"
    resumed, head = continued(options, tmp_path, "rtl-recheck")
    before = synthesis_billing(options["run"].objects, head)
    checks_seen = []

    def real_check(inputs, **kwargs):
        checks_seen.append(inputs.component)
        assert inputs.component == "rtl"
        return compare(inputs, **kwargs)

    monkeypatch.setattr(checks, "compare_sources", real_check)
    monkeypatch.setattr(synthesis_module, "execute_role", no_roles)
    monkeypatch.setattr(rtl_module, "execute_role", no_roles)
    config = SynthesisConfiguration(resumed["plan"], options["model_admission"].policy)
    config_path, head_path = tmp_path / "configuration.json", tmp_path / "head.json"
    config_path.write_bytes(config.to_bytes())
    head_path.write_bytes(canonical_json(head.as_dict()))
    args = ["--stage", "synthesis", "--configuration", str(config_path),
            "--source-objects", str(options["run"].objects.directory), "--run-directory", str(resumed["run"].directory),
            "--continue-from", str(head_path)]
    assert main([*args, "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated"
    assert not resumed["run"].directory.exists() and checks_seen == []
    assert main(args) == 0
    output = parse_json(capsys.readouterr().out.encode())
    assert output["status"] == "review_ready"
    result = SynthesisResult.from_bytes(resumed["run"].objects.read(BlobRef.from_dict(output["result"])))
    assert checks_seen == ["rtl"]
    assert result.drafts == first.drafts and result.checks == first.checks and result.phases == first.phases
    new_rtl, ledgers, artifact = auxiliary(resumed, result)
    assert new_rtl.status == "passed" and artifact is not None and ledgers == ()
    assert new_rtl.candidates == built.candidates
    assert new_rtl.phases[:-1] == built.phases[:-1] and new_rtl.checks != built.checks
    package, policy = package_and_policy(resumed)
    admitted = admit_package(package=package, policy=policy, reader=resumed["run"].objects)
    assert admitted.disposition.outcome == "include" and admitted.task.reference_rtl == artifact
    latest, closure = resumed["run"].seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(resumed["run"].objects, (*closure, package, policy))
    assert synthesis_billing(exported, latest) == before
    assert admit_package(package=package, policy=policy, reader=exported) == admitted
    assert load_continuation(exported, head).rtl_recheck == first.rtl_head
    assert synthesize(**{**resumed, "reader": Unavailable()}, continuation_from=head) == result
    assert checks_seen == ["rtl"] and options["run"].seal("synthesis")[0] == head


@pytest.mark.isolation
@pytest.mark.parametrize("after_retry,budget", [(None, 3), (0, 3), (1, 3), (0, 2)])
def test_rtl_recheck_keeps_history_and_accounts_only_new_authors(tmp_path, monkeypatch, after_retry, budget):
    # An earlier real failure/revision is retained as well as the latest author.
    options = setup_rtl(tmp_path / "first", [rtl_replies(), rtl_replies(), rtl_replies()], revisions=budget)
    scores = iter((0, None))
    def initial_check(inputs, **kwargs):
        if inputs.component == "behavior":
            return ComponentJudgment(1, "pair")
        score = next(scores)
        return unavailable() if score is None else ComponentJudgment(score, "fixture")

    monkeypatch.setattr(checks, "compare_sources", initial_check)
    first = synthesize(**options)
    old, _, _ = auxiliary(options, first)
    assert old.status == "check_unavailable" and len(old.candidates) == 2
    resumed, head = continued(options, tmp_path, "retry")
    before = synthesis_billing(options["run"].objects, head)
    scores = iter((after_retry, 1))
    checks_seen, roles_seen = [], []
    execute = rtl_module.execute_role

    def compare(inputs, **kwargs):
        assert inputs.component == "rtl"
        checks_seen.append(inputs.component)
        score = next(scores)
        return unavailable() if score is None else ComponentJudgment(score, "rechecked")

    def execute_new(job, **kwargs):
        roles_seen.append(kwargs["revision"])
        assert after_retry == 0 and budget == 3 and kwargs["revision"] == 2
        return execute(job, **kwargs)

    monkeypatch.setattr(checks, "compare_sources", compare)
    monkeypatch.setattr(synthesis_module, "execute_role", no_roles)
    monkeypatch.setattr(rtl_module, "execute_role", execute_new)
    result = synthesize(**resumed, continuation_from=head)
    built, added_ledgers, _ = auxiliary(resumed, result)
    assert result.phases == first.phases and result.drafts == first.drafts and result.checks == first.checks
    assert built.candidates[:2] == old.candidates and built.checks[:1] == old.checks[:1]
    repairs = after_retry == 0 and budget == 3
    expected = ("check_unavailable" if after_retry is None else
                "revision_limit" if after_retry == 0 and not repairs else "passed")
    assert built.status == expected
    assert roles_seen == ([2] if repairs else [])
    assert len(checks_seen) == (2 if repairs else 1)
    latest, _ = resumed["run"].seal("synthesis")
    from behave.agent.trajectory import CostLedger

    assert synthesis_billing(resumed["run"].objects, latest) == CostLedger.combine((before, *added_ledgers))
    package, policy = package_and_policy(resumed)
    assert admit_package(package=package, policy=policy, reader=resumed["run"].objects).disposition.outcome == (
        "include" if expected == "passed" else "pending")
    if after_retry is None:
        third, second_head = continued(resumed, tmp_path, "third")
        final = synthesize(**third, continuation_from=second_head)
        assert construction_status(third["run"].objects, final)[0] == "review_ready"
        assert synthesis_billing(third["run"].objects, third["run"].seal("synthesis")[0]) == before
        assert roles_seen == []


@pytest.mark.isolation
@pytest.mark.parametrize("score,cleanup", [(0, CleanupState.COMPLETE), (1, CleanupState.COMPLETE),
                                          (None, CleanupState.INCOMPLETE)])
def test_rtl_recheck_rejects_scored_or_unquiescent_branches(tmp_path, monkeypatch, score, cleanup):
    options = setup_rtl(tmp_path, [rtl_replies()])
    monkeypatch.setattr(checks, "compare_sources", lambda inputs, **kwargs:
                        ComponentJudgment(1, "pair") if inputs.component == "behavior" else
                        ComponentJudgment(score, "fixture", origin="verifier" if score is None else None, cleanup=cleanup))
    synthesize(**options)
    with pytest.raises(RecordError, match="no-result check or task-review request"):
        load_continuation(options["run"].objects, options["run"].seal("synthesis")[0])
