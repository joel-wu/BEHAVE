"""Recovery uses sealed local evidence; generated reports remain admissible."""

from dataclasses import replace

import pytest
from test_attempts import Scripted
from test_release_admission import candidate
from test_synthesis import reader_replies
from test_synthesis_source_review import read_comparison, prepared, review_reply

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError
from behave.data import checks
from behave.data.publication import publish_release
from behave.data.synthesis import seal_review_package, synthesize
from behave.task_execution.judgment import ComponentJudgment


class Offline:
    def read(self, ref):
        pytest.fail("sealed recovery must not read the upstream store")


def finished(options, author, monkeypatch):
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    return synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
                      source_reviewer_backend=Scripted(read_comparison(), review_reply()))


def recover(options):
    return synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted())


@pytest.mark.parametrize("role", ["author", "reader", "check", "source_review"])
@pytest.mark.parametrize("pooled", [False, True])
def test_recovery_checks_every_phase_even_without_admission(tmp_path, monkeypatch, role, pooled):
    options, author, _ = prepared(tmp_path, monkeypatch)
    if pooled:
        options["check_admission"] = ResourcePool(AdmissionPolicy("checks", 1))
    finished(options, author, monkeypatch)
    journal = options["run"].journal("revisions", "r0", role)
    journal.append(journal.load(), "unexpected", {})
    with pytest.raises(RecordError, match="changed after termination"):
        recover(options)
    with pytest.raises(RecordError, match="changed after termination"):
        seal_review_package(options["run"])


@pytest.mark.parametrize("interrupted", [False, True])
def test_synthesis_recovers_without_upstream(tmp_path, monkeypatch, interrupted):
    from behave.data import synthesis as synthesis_module

    options, author, _ = prepared(tmp_path, monkeypatch)
    original = synthesis_module.run_source_review
    class Crash(BaseException):
        pass
    def crashed(**kwargs):
        original(**kwargs)
        raise Crash()
    if interrupted:
        monkeypatch.setattr(synthesis_module, "run_source_review", crashed)
        with pytest.raises(Crash):
            finished(options, author, monkeypatch)
        monkeypatch.setattr(synthesis_module, "run_source_review", original)
    else:
        finished(options, author, monkeypatch)
    result = recover({**options, "reader": Offline()})
    assert result.status == "review_ready"
    assert recover({**options, "reader": Offline()}) == result


def test_completed_publication_recovers_without_upstream(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    options = {"release_id": "r1", "policy": c.policy_ref, "packages": (c.ref,), "reader": c.store,
               "destination": JournalDirectory(tmp_path / "release")}
    result = publish_release(**options)
    assert publish_release(**{**options, "reader": Offline()}) == result
    with pytest.raises(RecordError, match="replay differs"):
        publish_release(**{**options, "reader": Offline(), "release_id": "changed"})


def test_publication_recovery_still_verifies_local_closure(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "release")
    options = {"release_id": "r1", "policy": c.policy_ref, "packages": (c.ref,), "reader": c.store,
               "destination": destination}
    publish_release(**options)
    # Simulate local damage while the upstream copy remains intact. Recovery
    # must not silently replace evidence or return an apparently healthy release.
    (destination.objects.directory / c.policy_ref.sha256).unlink()
    with pytest.raises(RecordError) as error:
        publish_release(**options)
    assert error.value.code == "content-unavailable"


@pytest.mark.parametrize("body", [b"", b" \n\t", b"\xff"])
def test_review_guidance_is_validated_before_spending_a_turn(tmp_path, body, monkeypatch):
    options, _, _ = prepared(tmp_path, monkeypatch)
    plan = options["plan"]
    options["plan"] = replace(plan, source_review=replace(plan.source_review, guidance=options["reader"].put(body)))
    with pytest.raises(RecordError) as error:
        recover(options)
    assert error.value.code in {"review-guidance-empty", "review-guidance-not-text"}
    assert options["run"].journal("synthesis").load() == ()
