"""A new model at one path is a new subject, not a cached score or analysis."""

import hashlib
import json
from pathlib import Path

import pytest

from behave.sim import api

from .test_behavior_public_exact_construction import (
    INVERTED_SOURCE,
    NATIVE_ONLY_SOURCE,
    SOURCE,
    _contract,
    _generation,
    _reference,
)
from .test_command_handler import _BaselineOnly

pytestmark = [pytest.mark.solver, pytest.mark.isolation]


def test_revisions_have_independent_scores_analyses_and_frozen_handles(tmp_path):
    path = tmp_path / "candidate.py"
    reference = _reference(SOURCE)
    contract = _contract()
    generation = _generation(contract)
    config = api.BehaviorConfig(path, file=path.name, timeout_s=90)
    records = {}

    def prepare(source):
        path.write_text(source)
        realization = api.realization_of_behavior_source(source, contract, file=path.name)
        task = api.admit(contract, realization, reference=reference)
        return task, api.prepare_behavior(
            task, config, generation, bounded_analysis=True, analysis_bound=1)

    def run(task, handle, name, *, passed, verdict):
        result = api.handle_command(handle.command_request(
            run_directory=tmp_path / name,
            baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
            providers=(_BaselineOnly(),), probe_budget=0,
            run_analysis=True, reference=reference,
            feasibility_timeout_ms=20_000, query_timeout_ms=20_000,
            analysis_wall_timeout_seconds=180))
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.evaluation.passed is passed, result.evaluation.as_dict()
        assert result.analysis.verdict is verdict, result.analysis.as_dict()
        assert result.analysis.concrete == result.evaluation.fingerprint
        assert all(row.trace.artifact == task.realization.identity
                   for row in result.evaluation.results)
        record = Path(result.record_path)
        image = json.loads(record.read_bytes())
        assert image["authority"]["evaluation"] == json.loads(json.dumps(
            result.evaluation.as_evidence_dict()))
        # Later commands may publish new views, but cannot replace prior decisions.
        assert record not in records
        records[record] = hashlib.sha256(record.read_bytes()).hexdigest()
        for previous, digest in records.items():
            assert hashlib.sha256(previous.read_bytes()).hexdigest() == digest
        return result

    original_task, original_handle = prepare(SOURCE)
    first = run(original_task, original_handle, "original", passed=True,
                verdict=api.Verdict.CLASSIFIED)
    changed_task, changed_handle = prepare(INVERTED_SOURCE)
    assert original_task.realization.identity != changed_task.realization.identity
    with pytest.raises(api.Refused) as mismatch:
        api.prepare_behavior(original_task, config, generation, bounded_analysis=True)
    assert mismatch.value.refusal.kind == "behavior-public-exact-artifact-mismatch"
    changed = run(changed_task, changed_handle, "changed", passed=False,
                  verdict=api.Verdict.CLASSIFIED)
    assert changed.evaluation.fingerprint != first.evaluation.fingerprint

    # This executes the old frozen source, not whatever now occupies its path.
    replayed = run(original_task, original_handle, "old-handle", passed=True,
                   verdict=api.Verdict.CLASSIFIED)
    # The evidence fingerprint includes per-invocation telemetry. Compare the
    # complete semantic image, retaining source, traces, comparison and goals.
    before = first.evaluation.as_evidence_dict()
    after = replayed.evaluation.as_evidence_dict()
    before.pop("telemetry")
    after.pop("telemetry")
    assert after == before

    native_task, native_handle = prepare(NATIVE_ONLY_SOURCE)
    assert native_task.realization.identity not in {
        original_task.realization.identity, changed_task.realization.identity}
    native = run(native_task, native_handle, "new-native-only", passed=True,
                 verdict=api.Verdict.REFUSED)
    assert native.analysis.evidence["refusal"]["kind"] == "statement-unsupported"
    assert path.read_text() == NATIVE_ONLY_SOURCE
