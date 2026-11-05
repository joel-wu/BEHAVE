from dataclasses import replace

import pytest
from test_judge import ScriptedJudge
from test_task_workflow import SUBMIT, setup

from behave.core.records import RecordError, canonical_json, parse_json
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.workflow import execute_task
from behave.train.training_data import load_candidate


@pytest.mark.isolation
@pytest.mark.parametrize("provider,status,body", [
    ("openai", 429, {"error": {"code": "credit_balance_exhausted"}}),
    ("anthropic", 429, {"error": {"type": "rate_limit_error",
                                  "details": {"error_code": "enforced_spend_limit_reached"}}}),
    ("fireworks", 402, {"error": "Payment required"}),
])
def test_billing_rejection_is_no_result_and_keeps_earlier_charge(tmp_path, publication, provider, status, body):
    program = SUBMIT[:SUBMIT.index("sys.stdout.buffer.write")] + f'''
from behave.agent.providers.http_errors import http_failure
from behave.agent.model import Charge
from behave.core.records import canonical_json
if request.turn == 0:
    reply = ModelReply('', calls, charge=Charge('0.125', 'USD', 'fixture/1'),
                       token_data={{'fixture_tokens':[1,2]}})
else:
    body = {body!r}
    reply = http_failure({provider!r}, {status}, body, canonical_json(body), {{}}, None)
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    job, run = setup(tmp_path, publication, program)
    judge = ScriptedJudge(ComponentJudgment(1, "ok"), ComponentJudgment(1, "ok"))
    report = execute_task(reader=publication.reader, job=job, run=run, judge=judge)
    candidate = load_candidate(run.objects, report.content)
    assert candidate.result.status == "no_result" and candidate.reward == 0
    assert candidate.result.score is None
    assert candidate.result.failure_stage == "solver"
    assert candidate.result.failure_code == f"model-permanent:{provider}-quota-exhausted"
    assert len(candidate.trajectory.turns) == 1
    assert candidate.trajectory.cost_by_currency() == {"USD": "0.125"}
    assert candidate.trajectory.unpriced_dispatches == 1
    assert execute_task(reader=publication.reader, job=job, run=run, judge=judge) == report


@pytest.mark.isolation
def test_native_context_stop_keeps_prior_turn_and_zero_reward(tmp_path, publication):
    program = SUBMIT[:SUBMIT.index("sys.stdout.buffer.write")] + r'''
from behave.agent.providers.http_errors import http_failure
from behave.core.records import canonical_json
if request.turn == 0:
    reply = ModelReply('', calls, token_data={'fixture_tokens':[1,2]})
else:
    body = {'object':'error', 'type':'BadRequestError', 'code':400,
            'message':"The input (8192 tokens) is longer than the model's context length (8192 tokens)."}
    reply = http_failure('chat', 400, body, canonical_json(body), {}, None)
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    job, run = setup(tmp_path, publication, program)
    judge = ScriptedJudge(ComponentJudgment(1, "ok"), ComponentJudgment(1, "ok"))
    report = execute_task(reader=publication.reader, job=job, run=run, judge=judge)
    candidate = load_candidate(run.objects, report.content)
    assert candidate.result.status == "no_submission" and candidate.reward == 0
    assert len(candidate.trajectory.turns) == 1
    assert candidate.trajectory.turns[0].token_data == {"fixture_tokens": (1, 2)}
    assert candidate.trajectory.turns[0].training_status == "recorded"
    assert execute_task(reader=publication.reader, job=job, run=run, judge=judge) == report


@pytest.mark.isolation
def test_turn_exclusion_survives_submission_reward_and_sealed_export(tmp_path, publication):
    # Deliberately opaque fixture tokens: the handoff reports "recorded", never
    # claims tokenizer/log-probability acceptance from these invented numbers.
    program = SUBMIT.replace("else:\n", "elif request.turn == 2:\n")
    program = program[:program.index("sys.stdout.buffer.write")] + r'''
if request.turn == 1:
    reply = ModelReply('truncated', training_exclusion='output-length', finish_reason='length')
else:
    reply = ModelReply('', calls, token_data={'fixture_tokens':[1,2]} if request.turn == 0 else None)
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    job, run = setup(tmp_path, publication, program)
    report = execute_task(reader=publication.reader, job=job, run=run,
                          judge=ScriptedJudge(ComponentJudgment(1, "ok"), ComponentJudgment(1, "ok")))
    candidate = load_candidate(run.objects, report.content)
    assert candidate.reward == 1
    assert [turn.training_status for turn in candidate.trajectory.turns] == ["recorded", "excluded", "unavailable"]
    assert candidate.trajectory.turns[1].exclusion == "output-length"
    assert candidate.trajectory.turns[0].token_data == {"fixture_tokens": (1, 2)}
    # An inconsistent accounting summary is rejected rather than changing masks.
    row = parse_json(run.objects.read(report.result.accounting))
    row["turns"][1]["exclusion"] = None
    altered = replace(report.result, accounting=run.objects.put(canonical_json(row)))
    with pytest.raises(RecordError, match="summary differs"):
        load_candidate(run.objects, run.objects.put(altered.to_bytes()))
