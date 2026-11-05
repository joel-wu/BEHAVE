"""Explicit historical source archives, not a bypass around current launch policy.

Scripted role replies and a synthetic worker receipt populate real journals and
provider ledgers. Production readers verify them unchanged. No retired process,
model command or namespace is launched by this fixture.
"""

from dataclasses import replace

from test_attempts import Scripted
from test_role_process import job_for

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.model import Charge
from behave.agent.model_ledger import ModelLedger
from behave.agent.process_worker import WorkerChannels, settled_attempt
from behave.agent.replay import Tape
from behave.core.artifacts import BlobRef
from behave.core.records import thaw_json
from behave.data.discovery import run_discovery
from behave.data.phase_journal import seal_process
from behave.data.role import DiscoveryInputs
from behave.data.selection import SelectionResult, run_selection, selection_view
from behave.data.selection_records import SourceSelection
from behave.data.synthesis_records import PhaseReceipt


def historical_job(inputs):
    return replace(job_for(inputs, []), shell_limits=None)


def archive_source_run(configuration, reader, run, replies, *, pending=False, receipt=None):
    job = configuration.job
    discovery = type(job.inputs) is DiscoveryInputs
    kind, role = ("discovery", "source_discoverer") if discovery else ("selection", "source_selector")
    refs = ((job.inputs.guidance, job.inputs.fetch_policy) if discovery else selection_view(reader, job.inputs).closure)
    objects = run.objects
    objects.publish(reader, refs)
    config_ref, job_ref = objects.put(configuration.to_bytes()), objects.put(job.to_bytes())
    tape = Tape(run.journal(kind))
    assert not tape.events, "fixture must not overwrite prior evidence"
    tape.record(kind + "_started", {"configuration": config_ref.as_dict()}, (config_ref, *refs))
    tape.record(kind + "_requested", {"job": job_ref.as_dict()}, (job_ref,))
    journal = run.journal("revisions", "r0", role)
    supervision = run.journal("revisions", "r0", role + "_supervision")
    pool = ResourcePool(configuration.model_admission) if configuration.model_admission else None
    provider = run.journal("revisions", "r0", role + "_provider") if pool else None
    channels = WorkerChannels(journal, supervision, pool, provider)

    def launch():
        if pending:
            raise AttemptInterrupted("fixture-owner-lost")
        if receipt is not None:
            return receipt, ()
        backend = Scripted(*(replace(reply, charge=Charge("0.01", "USD", "fixture")) for reply in replies))
        if provider is not None:
            ledger = ModelLedger(provider, {"schema": "behave.model-relay/1", "spec": job.attempt.as_dict(),
                "command": job.model.as_dict(), "admission": configuration.model_admission.as_dict()})
            scripted = backend

            class RecordedProvider:
                def generate(self, request):
                    return ledger.dispatch(request, scripted.generate)

            backend = RecordedProvider()
        (run_discovery if discovery else run_selection)(inputs=job.inputs, reader=objects,
            journal=journal, spec=job.attempt, backend=backend)
        return {"schema": "behave.construction-settlement/1", "cleanup": "complete", "status": "exited",
                "returncode": 0, "elapsed_seconds": .01, "interruption": None, "supervision_errors": []}, ()

    decision = None
    try:
        attempt = settled_attempt(namespace="construction", spec=job.attempt, journal=journal,
            supervision=supervision, payload=channels.payload(job_ref), dependencies=(job_ref, *refs), launch=launch)
        status = "completed" if discovery and attempt.reason == "final_action" else "unfinished"
        code = kind + "-" + attempt.reason
        if not discovery and attempt.final_action is not None:
            decision = BlobRef.from_dict(thaw_json(attempt.final_action)["selection"])
            selected = SourceSelection.from_bytes(objects.read(decision)).selected
            status, code = "selected" if selected else "no_selection", "selection-complete"
    except AttemptInterrupted as error:
        if pending:
            raise
        status, code = "interrupted", error.code
    process, auxiliary = seal_process(run, 0, role)
    role_head, closure = run.seal("revisions", "r0", role)
    phase = PhaseReceipt(0, role, role_head, process)
    if discovery:
        tape.record("discovery_finished", {"phase": phase.as_dict(), "status": status, "code": code},
                    (*closure, *auxiliary))
    else:
        tape.record("selection_settled", {"phase": phase.as_dict(),
            "decision": None if decision is None else decision.as_dict()},
            (*closure, *auxiliary, *(() if decision is None else (decision,))))
        result = objects.put(SelectionResult(config_ref, status, code, decision, phase).to_bytes())
        tape.record("selection_finished", {"result": result.as_dict()}, (result,))
    tape.finish()
    return run.seal(kind)[0]
