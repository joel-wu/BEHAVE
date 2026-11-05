"""Synthesis inspection reuses one verified result, not a second journal read."""

import json
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from test_attempts import Scripted
from test_synthesis import reader_replies, setup

from behave.agent.journal import Event, load_chain
from behave.agent.model import Charge
from behave.cli.inspect import inspect_record
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks, process_evidence
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


def synthesis_inputs(directory, monkeypatch):
    options, author, _ = setup(directory)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(1, "fixture"))
    result = synthesize(**options,
        author_backend=Scripted(*(replace(reply, charge=Charge("0.01", "USD", "fixture")) for reply in author)),
        reader_backend=Scripted(*reader_replies()))
    head, closure = options["run"].seal("synthesis")
    copied = DirectoryStore(directory / "exported")
    copied.publish(options["run"].objects, closure)
    return copied, head, result


class Reads:
    def __init__(self, source):
        self.source, self.calls = source, Counter()

    def read(self, ref):
        self.calls[ref] += 1
        return self.source.read(ref)


def test_inspected_coordinator_is_read_once(tmp_path, monkeypatch):
    objects, head, result = synthesis_inputs(tmp_path, monkeypatch)
    expected = process_evidence.synthesis_billing(objects, head)
    reader = Reads(objects)
    view = inspect_record("synthesis", reader, head)
    assert reader.calls[head] == 1
    assert view == {"result": parse_json(result.to_bytes()), "billing": {
        "known_estimates_by_currency": thaw_json(expected.cost_by_currency()),
        "unpriced_dispatches": expected.unpriced_dispatches,
        "unresolved_dispatches": expected.unresolved_dispatches}}


def test_shared_reader_retains_result_and_exact_billing_without_dispatch(tmp_path, monkeypatch):
    objects, head, result = synthesis_inputs(tmp_path, monkeypatch)
    expected = process_evidence.synthesis_billing(objects, head)
    before = {p.name: p.read_bytes() for p in objects.directory.iterdir() if p.is_file()}
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: pytest.fail("inspection cannot compare"))
    loaded, billing = process_evidence.load_synthesis(objects, head)
    assert loaded.to_bytes() == result.to_bytes() and billing == expected
    assert billing.cost_by_currency() == {"USD": "0.06"}
    assert {p.name: p.read_bytes() for p in objects.directory.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("damage", ["unfinished", "plan", "handoff"])
def test_shared_reader_keeps_full_history_checks(tmp_path, monkeypatch, damage):
    objects, head, result = synthesis_inputs(tmp_path, monkeypatch)
    events = load_chain(objects, head)
    if damage == "unfinished":
        changed = events[:-1]
    else:
        changed = list(events)
        if damage == "plan":
            from behave.data.synthesis_records import SynthesisPlan

            plan = SynthesisPlan.from_bytes(objects.read(result.plan))
            new_plan = objects.put(replace(plan, max_revisions=plan.max_revisions + 1).to_bytes())
            new_result = objects.put(replace(result, plan=new_plan).to_bytes())
            last = changed[-1]
            changed[-1] = replace(last, payload={"result": new_result.as_dict()}, artifacts=(new_result,))
        else:
            index = next(i for i, event in enumerate(changed)
                         if event.kind == "phase_settled" and event.payload["phase"]["role"] == "author")
            payload = thaw_json(changed[index].payload)
            payload["output"] = objects.put(b"different handoff").as_dict()
            changed[index] = replace(changed[index], payload=payload)
    parent = None
    for event in changed:
        parent = objects.put(Event(event.sequence, parent, event.kind, event.payload, event.artifacts).to_bytes())
    for operation in (process_evidence.load_synthesis, process_evidence.synthesis_billing):
        with pytest.raises(RecordError) as caught:
            operation(objects, parent)
        assert caught.value.code == "synthesis-process-mismatch"


def test_a_previous_read_does_not_hide_missing_evidence(tmp_path, monkeypatch):
    objects, head, _ = synthesis_inputs(tmp_path, monkeypatch)
    process_evidence.load_synthesis(objects, head)
    events = load_chain(objects, head)
    lost = next(event.artifacts[0] for event in events if event.artifacts)
    (objects.directory / lost.sha256).rename(tmp_path / "retained-but-unavailable")
    with pytest.raises((OSError, RecordError)):
        process_evidence.load_synthesis(objects, head)


SYNTHESIS_INSPECTION_PROBE = """
import json
import subprocess
import sys
from pathlib import Path

def no_process(*args, **kwargs):
    raise AssertionError('inspection launched a process')

subprocess.Popen = no_process
from behave.cli.inspect import inspect_record, main
from behave.core.artifacts import BlobRef, DirectoryReader
source = Path(sys.argv[1])
request = json.loads(source.read_text())
head = BlobRef.from_dict(request['head'])
view = inspect_record('synthesis', DirectoryReader(request['objects']), head)
assert view == request['expected']
reference = source.with_suffix('.ref.json')
reference.write_text(json.dumps(request['head']))
assert main(['--kind', 'synthesis', '--objects', request['objects'], '--record', str(reference)]) == 0
"""


def inspection_packet(directory, monkeypatch):
    objects, head, result = synthesis_inputs(directory, monkeypatch)
    ledger = process_evidence.synthesis_billing(objects, head)
    request = directory / "inspect.json"
    request.write_bytes(canonical_json({"objects": str(objects.directory), "head": head.as_dict(),
        "expected": {"result": parse_json(result.to_bytes()), "billing": {
            "known_estimates_by_currency": thaw_json(ledger.cost_by_currency()),
            "unpriced_dispatches": ledger.unpriced_dispatches,
            "unresolved_dispatches": ledger.unresolved_dispatches}}}))
    return request


def test_inspection_from_exported_objects_without_execution_imports(tmp_path, monkeypatch):
    request = inspection_packet(tmp_path, monkeypatch)
    source = Path(__file__).resolve().parents[2] / "src"
    bootstrap = f"import sys; sys.path.insert(0, {str(source)!r})\n"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap + SYNTHESIS_INSPECTION_PROBE, str(request)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["status"] == "review_ready"
