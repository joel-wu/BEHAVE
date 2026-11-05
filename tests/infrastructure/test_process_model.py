"""Real broker processes; no paid provider, GPU or untrusted source import."""

import fcntl
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from behave.agent import AttemptControl, AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.model import ModelRequest
from behave.agent.process_model import ModelCommand, ProcessModelBackend
from behave.agent.trajectory import trajectory
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore

ROOT = str(Path(sys.modules["behave"].__file__).resolve().parent.parent)
PRELUDE = '''
import sys
from behave.agent.model import Charge, ModelFailure, ModelReply, ModelRequest, ToolCall
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json, canonical_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
def send(result):
    sys.stdout.buffer.write(encode_model_outcome(request, result))
    sys.stdout.buffer.flush()
'''


def command(program, **kwargs):
    return ModelCommand((sys.executable, "-I", "-B", "-c",
                         "import sys; sys.path.insert(0,sys.argv[1]); exec(sys.argv[2])", ROOT, PRELUDE + program),
                        "fixture/1", **kwargs)


def request(cmd, *, seconds=3):
    return ModelRequest("a:0:0", 0, 0, ({"role": "user", "content": "test"},), (),
                        {"model": "fixture", "transport": cmd.identity}, seconds)


def run(tmp_path, cmd, *, seconds=3, control=None, journal=None):
    journal = journal or AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("a", "test", {"model": "fixture", "transport": cmd.identity},
                       request_timeout_seconds=seconds)
    return journal, run_attempt(journal=journal, spec=spec, backend=ProcessModelBackend(cmd),
                                initial_messages=({"role": "user", "content": "test"},), control=control)


@pytest.mark.isolation
def test_process_broker_records_exact_outcome_cost_and_never_calls_on_cold_replay(tmp_path):
    path = str(tmp_path / "calls")
    cmd = command(f'''
with open({path!r}, 'a') as stream: stream.write('called\\n')
send(ModelReply('done', usage={{'input_tokens': 7}}, raw=b'raw\\x00bytes',
                charge=Charge('0.03', 'USD', 'fixture'), token_data={{'ids': [1,2]}}))
''')
    journal, result = run(tmp_path, cmd)
    assert result.reason == "stopped"
    view = trajectory(journal)
    assert view.cost_by_currency() == {"USD": "0.03"}
    assert journal.objects.read(view.turns[0].raw_response) == b"raw\x00bytes"
    assert run(tmp_path, cmd, journal=journal)[1] == result
    assert Path(path).read_text().splitlines() == ["called"]


@pytest.mark.isolation
@pytest.mark.parametrize("ending,code", [
    ("import time; time.sleep(60)", "timeout"),
    ("import os; os._exit(0)", "invalid-response"),
    ("raise RuntimeError('secret-exception-text')", "failed"),
    ("import os,time\nwhile True:\n os.write(1,b'x'*4096)\n time.sleep(.01)", "timeout"),
])
def test_broken_broker_is_unknown_not_retried_or_zero(tmp_path, ending, code):
    path = str(tmp_path / "calls")
    cmd = command(f"with open({path!r}, 'a') as f: f.write('one\\n')\n" + ending)
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    with pytest.raises(AttemptInterrupted, match="model-unknown:model-worker-" + code):
        run(tmp_path, cmd, journal=journal)
    with pytest.raises(AttemptInterrupted):
        run(tmp_path, cmd, journal=journal)
    assert Path(path).read_text().splitlines() == ["one"]
    assert not any(e.kind in {"terminal", "tool_requested"} for e in journal.load())
    view = trajectory(journal)
    assert view.unresolved_dispatches == 1 and view.unpriced_dispatches == 1
    event = journal.load()[-1]
    assert b"secret-exception-text" not in canonical_json(dict(event.payload))


@pytest.mark.isolation
def test_slow_body_cannot_extend_request_deadline():
    cmd = command("import os,time\nwhile True:\n os.write(1,b'x')\n time.sleep(.05)")
    got = ProcessModelBackend(cmd).generate(request(cmd, seconds=1))
    assert got.kind == "unknown" and got.code == "model-worker-timeout"
    receipt = parse_json(got.raw)
    assert receipt["budget_seconds"] == 1
    assert receipt["elapsed_seconds"] < 8  # Includes the separately bounded reap.


@pytest.mark.isolation
def test_attempt_retains_individual_model_request_timeout(tmp_path):
    cmd = command("import time; time.sleep(60)")
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("a", "test", {"transport": cmd.identity},
                       request_timeout_seconds=1)
    with pytest.raises(AttemptInterrupted, match="model-unknown:model-worker-timeout"):
        run_attempt(journal=journal, spec=spec, backend=ProcessModelBackend(cmd),
                    initial_messages=({"role": "user", "content": "test"},))
    raw_ref = journal.load()[-1].payload["raw"]
    from behave.core.artifacts import BlobRef

    receipt = parse_json(journal.objects.read(BlobRef.from_dict(dict(raw_ref))))
    assert 0 < receipt["budget_seconds"] <= 1


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["import time; time.sleep(60)", "raise SystemExit(1)"])
@pytest.mark.parametrize("amount", ["0.4", "1e-120", "1e60"])
def test_valid_stdout_before_abnormal_exit_retains_bill_but_never_executes_tools(tmp_path, ending, amount):
    from decimal import Decimal

    cmd = command(f'''
send(ModelReply('', (ToolCall('a', 'write_file', {{'path':'x','content':'x'}}),),
                usage={{'input_tokens':9}}, charge=Charge({amount!r},'USD','fixture')))
''' + ending)
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    with pytest.raises(AttemptInterrupted, match="model-unknown"):
        run(tmp_path, cmd, journal=journal)
    assert Decimal(trajectory(journal).cost_by_currency()["USD"]) == Decimal(amount)
    assert trajectory(journal).unresolved_dispatches == 1
    assert not any(e.kind == "tool_requested" for e in journal.load())


@pytest.mark.isolation
def test_malformed_calls_preserve_bound_usage_without_json_repair():
    cmd = command('''
row = parse_json(encode_model_outcome(request, ModelReply('',
    (ToolCall('a','list_files',{}),), usage={'output_tokens':2}, charge=Charge('0.1','USD','fixture'))))
row['result']['calls'] *= 2
sys.stdout.buffer.write(canonical_json(row))
''')
    got = ProcessModelBackend(cmd).generate(request(cmd))
    assert got.code == "model-worker-invalid-response" and got.kind == "unknown"
    assert got.usage["output_tokens"] == 2 and got.charge.amount == "0.1"


@pytest.mark.isolation
@pytest.mark.parametrize("broken", ["charge", "usage", "raw_base64"])
def test_evidence_fields_survive_independently_when_the_bound_envelope_is_unusable(broken):
    cmd = command('''
row = parse_json(encode_model_outcome(request, ModelReply('done',
    usage={'output_tokens':2}, charge=Charge('0.1','USD','fixture'))))
''' + ("row['raw_base64']='not-base64!'\n" if broken == "raw_base64" else f"row['result'][{broken!r}]=[]\n") + '''
sys.stdout.buffer.write(canonical_json(row))
''')
    got = ProcessModelBackend(cmd).generate(request(cmd))
    assert got.kind == "unknown"
    assert (got.charge is not None) == (broken != "charge")
    assert bool(got.usage) == (broken != "usage")


@pytest.mark.isolation
def test_response_for_another_request_cannot_supply_usage_or_actions():
    cmd = command('''
from dataclasses import replace
request = replace(request, call_id='some-other-call')
send(ModelReply('done', usage={'output_tokens':2}, charge=Charge('0.1','USD','fixture')))
''')
    got = ProcessModelBackend(cmd).generate(request(cmd))
    assert got.code == "model-worker-invalid-response" and got.kind == "unknown"
    assert got.charge is None and not got.usage


@pytest.mark.isolation
@pytest.mark.parametrize("request_seconds", [8, None])
def test_cancel_running_broker_reaps_detached_descendant_and_does_not_dispatch_again(tmp_path, request_seconds):
    import socket
    from tempfile import TemporaryDirectory

    cancelled = Event()
    path = str(tmp_path / "child.lock")
    # Synchronize on a live child, not an arbitrary sleep before cancellation.
    with TemporaryDirectory(prefix="model-ready-", dir="/tmp") as scratch:
        address = str(Path(scratch) / "ready.sock")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(address)
            listener.listen(1)
            listener.settimeout(10)
            cmd = command(f'''
import fcntl, os, socket, subprocess, time
fd = os.open({path!r}, os.O_CREAT|os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
r,w = os.pipe()
subprocess.Popen([sys.executable,'-c','import os,sys,time;os.write(int(sys.argv[1]),b"1");time.sleep(60)',str(w)],
                 pass_fds=(fd,w), start_new_session=True)
os.close(w)
assert os.read(r,1)==b'1'
with socket.socket(socket.AF_UNIX) as peer:
    peer.connect({address!r})
    peer.sendall(b'1')
time.sleep(60)
''')
            journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(run, tmp_path, cmd, seconds=request_seconds,
                                      control=AttemptControl(cancelled.is_set), journal=journal)
                try:
                    peer, _ = listener.accept()
                    with peer:
                        assert peer.recv(1) == b"1"
                finally:
                    cancelled.set()
                with pytest.raises(AttemptInterrupted, match="model-unknown:model-worker-cancelled"):
                    pending.result(timeout=15)
            with open(path, "rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(AttemptInterrupted, match="model-unknown:model-worker-cancelled"):
                run(tmp_path, cmd, seconds=request_seconds, journal=journal)


def test_transport_mismatch_never_launches(monkeypatch):
    from behave.isolation import service

    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("no launch"))
    cmd = command("send(ModelReply('done'))")
    backend = ProcessModelBackend(cmd)
    changed = replace(request(cmd), model_policy={"transport": "wrong"})
    assert backend.generate(changed).code == "model-transport-mismatch"


@pytest.mark.parametrize("field,value", [("network", "auto")])
def test_transport_bounds_are_validated(field, value):
    with pytest.raises(ValueError):
        command("pass", **{field: value})


def test_request_owns_immutable_copies_and_roundtrips():
    policy = {"model": "fake"}
    messages = [{"role": "user", "content": "original"}]
    got = ModelRequest("a", 0, 0, messages, [], policy, 3)
    messages[0]["content"] = "changed"
    policy["model"] = "changed"
    assert got.messages[0]["content"] == "original" and got.model_policy["model"] == "fake"
    assert ModelRequest.from_dict(parse_json(canonical_json(got.as_dict()))) == got
