"""Coordinator-owned model queue with real local sockets and provider processes."""

import fcntl
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from test_chat_transport import config, reply, send, server
from test_judge import ScriptedJudge
from test_process_model import command
from test_task_workflow import STOP, setup

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.local_rpc import send_frame
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.model_relay import ModelRelay
from behave.agent.relay_wire import RelayModelBackend
from behave.agent.trajectory import trajectory
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.task_execution.workflow import execute_task
from behave.train.training_data import load_candidate


def wait_for(predicate, seconds=5):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= end:
            pytest.fail("condition did not become true")
        Event().wait(.01)


def make(tmp_path, cmd, pool, identity="a", seconds=10, policy=None):
    spec = AttemptSpec(identity, "solver", policy or {"model": "fixture", "transport": cmd.identity},
                        request_timeout_seconds=seconds)
    journal = AttemptJournal(tmp_path / "provider", DirectoryStore(tmp_path / "objects"))
    relay = ModelRelay(spec=spec, command=cmd, journal=journal, pool=pool, deadline=time.monotonic() + 30)
    request = ModelRequest(identity + ":0:0", 0, 0, ({"role": "user", "content": "fixture"},), (),
                            spec.model_policy, seconds)
    return relay, request, journal


def test_relay_dispatch_index_is_not_the_network_retry_budget():
    spec = AttemptSpec("window", "solver", {}, max_retries=0)
    assert spec.request_timeout_seconds is None
    relay = SimpleNamespace(spec=spec)
    request = ModelRequest("window:4:3", 4, 3, (), (), spec.model_policy, spec.request_timeout_seconds)
    assert ModelRelay._valid(relay, request)
    assert not ModelRelay._valid(relay, replace(request, call_id="other:4:3"))
    assert not ModelRelay._valid(relay, replace(request, turn=60, call_id="window:60:3"))
    assert ModelRequest.from_dict(request.as_dict()) == request


@pytest.mark.isolation
def test_local_delivery_and_admission_do_not_race_request_timeout(tmp_path, monkeypatch):
    from behave.agent.model_relay import ProcessModelBackend

    cmd = command("send(ModelReply('done'))")
    pool = ResourcePool(AdmissionPolicy("models", 1))
    owner, req, _journal = make(tmp_path, cmd, pool, seconds=1)
    owner.deadline = None
    monkeypatch.setattr(ProcessModelBackend, "generate", lambda self, request: ModelReply("done"))
    with owner, ThreadPoolExecutor(max_workers=1) as workers:
        with pool.acquire():
            pending = workers.submit(RelayModelBackend(owner.endpoint).generate, req)
            wait_for(lambda: pool.counts == (1, 1))
            Event().wait(1.1)
            assert not pending.done()
        assert pending.result(timeout=5) == ModelReply("done")


@pytest.mark.isolation
def test_supervised_solver_context_evictions_pass_relay_with_zero_network_retries(tmp_path, publication):
    from test_chat_transport import call

    from behave.data.published import load_published_task
    from behave.task_execution.solver import SolverJob, run_solver_process

    def answer(handler, requests, stopped):
        count = len(requests)
        if count <= 4:
            send(handler, reply((call(str(count), "run_command", {"command": "printf ready"}),)))
        elif count <= 7:
            send(handler, {"error": {"code": "context_length_exceeded", "message": "context length exceeded"}}, 400)
        else:
            send(handler, reply())

    with server(answer) as (url, observed):
        endpoint = config(url)
        public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
        spec = AttemptSpec("window", "solver", endpoint.model_policy(), max_turns=5, max_retries=0,
                           request_timeout_seconds=15)
        job = SolverJob(public, spec, endpoint.command())
        store = DirectoryStore(tmp_path / "objects")
        journal = AttemptJournal(tmp_path / "solver", store)
        supervisor = AttemptJournal(tmp_path / "supervisor", store)
        provider = AttemptJournal(tmp_path / "provider", store)
        pool = ResourcePool(AdmissionPolicy("models", 1))
        result = run_solver_process(job, journal=journal, supervision=supervisor,
                                    model_pool=pool, provider=provider)
    assert result.attempt.reason == "stopped" and result.attempt.turns == 5
    assert len(observed) == 8
    statuses = [body["messages"][-1]["content"] for _, _, body in observed]
    assert "turn 1 of 5. Model turns remaining (including this turn): 5." in statuses[0]
    assert all("turn 5 of 5. Model turns remaining (including this turn): 1." in status for status in statuses[4:])
    assert len(set(statuses[4:])) == 1
    assert [len(body["messages"]) for _, _, body in observed[4:]] == [15, 12, 9, 6]
    for (_, _, previous), (_, _, current) in pairwise(observed[:5]):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]
    evictions = [e for e in journal.load() if e.kind == "context_evicted"]
    assert len(evictions) == 3
    assert len(trajectory(journal).turns) == 5
    assert len([e for e in provider.load() if e.kind == "model_requested"]) == 8
    assert pool.counts == (0, 0)


@pytest.mark.isolation
def test_two_relays_share_one_provider_slot_and_repeated_calls_only_read_receipts(tmp_path):
    reached, release, lock = Event(), Event(), Lock()
    active = maximum = 0
    def answer(handler, observed, stopped):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        reached.set()
        release.wait(10)
        send(handler, reply())
        with lock:
            active -= 1
    pool = ResourcePool(AdmissionPolicy("models", 1))
    with server(answer) as (url, observed), ExitStack() as stack:
        endpoint = config(url)
        cmd = endpoint.command()
        first, r1, j1 = make(tmp_path / "a", cmd, pool, "a", policy=endpoint.model_policy())
        second, r2, j2 = make(tmp_path / "b", cmd, pool, "b", policy=endpoint.model_policy())
        stack.enter_context(first)
        stack.enter_context(second)
        assert Path(first.endpoint).parent.parent == Path("/tmp")
        assert Path(first.endpoint).stat().st_mode & 0o777 == 0o600
        with ThreadPoolExecutor(max_workers=2) as workers:
            f1 = workers.submit(RelayModelBackend(first.endpoint).generate, r1)
            assert reached.wait(5)
            f2 = workers.submit(RelayModelBackend(second.endpoint).generate, r2)
            try:
                wait_for(lambda: pool.counts == (1, 1))
                assert not any(e.kind == "model_requested" for e in j2.load())
            finally:
                release.set()
            assert type(f1.result(10)) is ModelReply
            assert type(f2.result(10)) is ModelReply
        assert maximum == 1 and len(observed) == 2
        assert type(RelayModelBackend(first.endpoint).generate(r1)) is ModelReply
        assert len(observed) == 2
        assert trajectory(j1).cost_by_currency() == trajectory(j2).cost_by_currency() == {"USD": "0.00006"}
    assert pool.counts == (0, 0)


@pytest.mark.isolation
def test_disconnected_waiter_never_dispatches_or_consumes_a_model_slot(tmp_path):
    pool = ResourcePool(AdmissionPolicy("models", 1))
    relay, request, journal = make(tmp_path, command("raise AssertionError('must not launch')"), pool)
    with relay, pool.acquire():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(relay.endpoint)
            stream.setblocking(False)
            send_frame(stream, canonical_json(request.as_dict()), deadline=time.monotonic() + 5)
            wait_for(lambda: pool.counts == (1, 1))
        wait_for(lambda: pool.counts == (1, 0))
        wait_for(lambda: journal.load()[-1].kind == "model_declined")
        assert not any(e.kind == "model_requested" for e in journal.load())
        assert trajectory(journal).cost_by_currency() == {}


@pytest.mark.isolation
def test_queue_expiry_is_not_a_provider_dispatch_or_an_unknown_charge(tmp_path):
    pool = ResourcePool(AdmissionPolicy("models", 1, wait_seconds=1))
    relay, request, journal = make(tmp_path, command("raise AssertionError('must not launch')"), pool)
    with relay, pool.acquire():
        outcome = RelayModelBackend(relay.endpoint).generate(request)
        assert outcome.kind == "permanent" and outcome.code == "resource-wait-expired"
        assert pool.counts == (1, 0)
    assert not any(e.kind == "model_requested" for e in journal.load())
    billing = trajectory(journal).billing
    assert (billing.unpriced_dispatches, billing.unresolved_dispatches) == (0, 0)


def test_nonquiescent_provider_cannot_publish_final_task_accounting(tmp_path, publication, monkeypatch):
    from behave.task_execution import workflow

    pool = ResourcePool(AdmissionPolicy("models", 1))
    relay, _, _ = make(tmp_path / "relay", command("pass"), pool)
    relay._listener = SimpleNamespace(close=lambda: None)
    relay._thread = SimpleNamespace(join=lambda **kw: None, is_alive=lambda: True)
    relay._scratch = SimpleNamespace(cleanup=lambda: pytest.fail("active ingress must not be deleted"))
    with pytest.raises(RecordError, match="not quiescent"):
        relay.__exit__()

    job, run = setup(tmp_path / "task", publication, STOP)
    def unfinished(*args, **kwargs):
        raise RecordError("model-relay-cleanup-incomplete", "provider ledger is not quiescent")
    monkeypatch.setattr(workflow, "run_solver_process", unfinished)
    with pytest.raises(RecordError, match="not quiescent"):
        execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(), model_pool=pool)
    assert [e.kind for e in run.journal("task").load()] == ["task_started"]


@pytest.mark.isolation
def test_disconnect_during_provider_call_preserves_observed_charge_and_reaps_children(tmp_path, monkeypatch):
    from behave.isolation import service

    seen = Event()
    original_read = service.os.read
    def observe_read(*args):
        data = original_read(*args)
        if b"inflight-fixture" in data:
            seen.set()
        return data
    monkeypatch.setattr(service.os, "read", observe_read)
    locked = tmp_path / "descendant.lock"
    cmd = command(f'''
import os, fcntl, subprocess, time
fd = os.open({str(locked)!r}, os.O_CREAT|os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
r,w = os.pipe()
subprocess.Popen([sys.executable, '-c', 'import os,sys,time;os.write(int(sys.argv[1]),b"1");time.sleep(60)', str(w)],
                 pass_fds=(fd,w), start_new_session=True)
os.close(w)
assert os.read(r,1) == b'1'
send(ModelReply('done', charge=Charge('0.07', 'USD', 'inflight-fixture')))
time.sleep(60)
''')
    pool = ResourcePool(AdmissionPolicy("models", 1))
    relay, request, journal = make(tmp_path, cmd, pool, seconds=20)
    with relay:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(relay.endpoint)
            stream.setblocking(False)
            send_frame(stream, canonical_json(request.as_dict()), deadline=time.monotonic() + 5)
            assert seen.wait(10), "cancel only after supervisor observes usage-bearing stdout"
        # The durable append precedes the relay's in-memory index update. Read
        # that index only after its single owner also releases the dispatch.
        wait_for(lambda: journal.load()[-1].kind == "model_settled" and pool.counts == (0, 0), seconds=10)
        outcome = relay.ledger.lookup(request)
        assert outcome.kind == "unknown" and outcome.code == "model-worker-cancelled"
        assert trajectory(journal).cost_by_currency() == {"USD": "0.07"}
        assert trajectory(journal).turns == ()
        wait_for(lambda: pool.counts == (0, 0))
        with locked.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert relay.ledger.dispatch(request, lambda _: pytest.fail("unknown provider cannot restart")) == outcome


@pytest.mark.isolation
def test_relay_rejects_other_attempts_without_launching(tmp_path):
    pool = ResourcePool(AdmissionPolicy("models", 1))
    cmd = command("raise AssertionError('must not launch')")
    relay, request, journal = make(tmp_path, cmd, pool)
    with relay:
        wrong = replace(request, call_id="foreign:0:0")
        assert RelayModelBackend(relay.endpoint).generate(wrong).code == "model-relay-request-mismatch"
    assert len(journal.load()) == 1 and pool.counts == (0, 0)


@pytest.mark.isolation
def test_lost_solver_reply_preserves_provider_billing_without_inventing_training_turn(tmp_path, publication, monkeypatch):
    from behave.agent import local_rpc
    job, run = setup(tmp_path, publication, STOP.replace("ModelReply('done')", "ModelReply('done', charge=Charge('0.03','USD','test'))"))
    # Deliver nothing after the provider outcome was durably stored. The solver
    # sees an IPC failure, while accounting can still recover the observed charge.
    monkeypatch.setattr(local_rpc, "send_frame", lambda *args, **kwargs: None)
    report = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(),
                          model_pool=ResourcePool(AdmissionPolicy("models", 1)))
    assert report.result.score is None and report.result.failure_stage == "solver"
    candidate = load_candidate(run.objects, report.content)
    assert candidate.trajectory.turns == ()
    assert candidate.billing.cost_by_currency() == {"USD": "0.03"}
    assert not hasattr(candidate.billing, "turns")
    assert candidate.billing.unresolved_dispatches == 0
    assert len([e for e in run.journal("provider").load() if e.kind == "model_requested"]) == 1
    assert execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(),
                         model_pool=ResourcePool(AdmissionPolicy("models", 1))) == report


@pytest.mark.isolation
def test_supervised_solver_uses_coordinator_provider_and_preserves_no_submission_zero(tmp_path, publication):
    job, run = setup(tmp_path, publication, STOP)
    pool = ResourcePool(AdmissionPolicy("models", 1))
    result = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(), model_pool=pool)
    assert result.result.score == 0 and result.result.status == "no_submission", result.result.as_dict()
    candidate = load_candidate(run.objects, result.content)
    assert len(candidate.trajectory.turns) == 1 and candidate.billing.unpriced_dispatches == 1
    assert pool.counts == (0, 0)
