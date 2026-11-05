"""OpenRouter uses the common hosted broker, history and spend account."""

from dataclasses import replace
from decimal import Decimal

import pytest

from behave.agent.model import ModelFailure, ModelReply
from behave.agent.providers.credentials import Secret
from behave.agent.providers.hosted_worker import exchange, request_body, response
from behave.agent.providers.http_transport import HttpResponse
from behave.core.records import canonical_json, parse_json, thaw_json
from test_hosted_providers import body, endpoint, request


def router_body(*, call=True):
    result = body('openrouter', call=call)
    message = result['choices'][0]['message']
    message.pop('reasoning_content')
    message['reasoning'] = 'readable state'
    message['reasoning_details'] = [
        {'type': 'reasoning.text', 'text': 'readable state', 'index': 0, 'signature': 'keep'},
        {'type': 'reasoning.encrypted', 'data': 'opaque', 'index': 1, 'id': 'native'}]
    result['provider'] = 'Alibaba'
    result['usage'].update(cost=0.000123, completion_tokens_details={'reasoning_tokens': 3})
    return result


def test_router_round_trip_preserves_complete_reasoning_and_tools():
    ep = endpoint('openrouter', parameters={'reasoning': {'effort': 'xhigh', 'exclude': False},
        'provider': {'order': ['Alibaba'], 'allow_fallbacks': False, 'require_parameters': True}})
    native = router_body()
    reply = response(ep, 200, canonical_json(native))
    assert isinstance(reply, ModelReply)
    restored = ModelReply.from_dict(reply.as_dict(), raw=reply.raw)
    messages = ({'role': 'user', 'content': 'test'}, restored.message(),
        {'role': 'tool', 'call_id': 'call', 'content': {'result': 'ok'}})
    packet = parse_json(request_body(ep, request(ep, messages)))
    assert packet['reasoning'] == {'effort': 'xhigh', 'exclude': False}
    assert packet['messages'][1]['reasoning_details'] == native['choices'][0]['message']['reasoning_details']
    assert packet['messages'][1]['reasoning'] == 'readable state'
    assert Decimal(reply.charge.amount) == Decimal('0.000123')
    assert reply.usage['normalized']['reasoning_output_tokens'] == 3
    assert reply.usage['provider_name'] == 'Alibaba'
    assert ep.url == 'https://openrouter.ai/api/v1/chat/completions'
    assert Secret('fixture').authorization('openrouter') == {'Authorization': 'Bearer fixture'}


@pytest.mark.parametrize('cost', [None, True, -1, 'NaN', 'Infinity', {}])
def test_invalid_actual_cost_is_not_replaced_by_list_price(cost):
    native = router_body(call=False)
    native['usage']['cost'] = cost
    reply = response(endpoint('openrouter'), 200, canonical_json(native))
    assert reply.charge is None
    assert reply.usage['pricing_error'] == 'openrouter-cost-missing-or-invalid'


def test_actual_cost_survives_malformed_model_output_and_usage():
    native = router_body(call=False)
    native['choices'][0]['message']['reasoning_details'] = 'invalid'
    native['usage'].pop('prompt_tokens')
    reply = response(endpoint('openrouter'), 200, canonical_json(native))
    assert isinstance(reply, ModelFailure)
    assert Decimal(reply.charge.amount) == Decimal('0.000123')


def test_zero_cost_and_cache_creation_are_retained():
    native = router_body(call=False)
    native['usage']['cost'] = 0
    native['usage']['prompt_tokens_details']['cache_write_tokens'] = 5
    reply = response(endpoint('openrouter'), 200, canonical_json(native))
    assert reply.charge.amount == '0'
    assert reply.usage['normalized']['cache_write_tokens'] == 5


def test_context_count_uses_one_token_and_caps_total_context(monkeypatch):
    import behave.agent.providers.chat_prefill as prefill
    import behave.agent.providers.hosted_worker as worker

    packets = []
    admitted = []
    monkeypatch.setattr(worker, 'wait_for_request', lambda ep: admitted.append('generate'))
    monkeypatch.setattr(prefill, 'wait_for_request', lambda ep: admitted.append('count'))
    def post(url, packet, **kwargs):
        packet = parse_json(packet)
        packets.append(packet)
        native = router_body(call=False)
        native['choices'][0]['finish_reason'] = 'length' if len(packets) == 1 else 'stop'
        if len(packets) == 1:
            native['usage']['completion_tokens'] = 1
            native['usage']['completion_tokens_details']['reasoning_tokens'] = 1
        return HttpResponse(200, canonical_json(native), None)

    monkeypatch.setattr(worker, 'load_credential', lambda _: Secret('fixture-key'))
    monkeypatch.setattr(worker, 'post_json', post)
    monkeypatch.setattr(prefill, 'post_json', post)
    ep = endpoint('openrouter', parameters={'reasoning': {'effort': 'xhigh'}})
    reply = exchange(ep, replace(request(ep), context_tokens=131072))
    assert isinstance(reply, ModelReply)
    assert [packet['max_tokens'] for packet in packets] == [1, 131062]
    assert all(packet['reasoning'] == {'effort': 'xhigh'} for packet in packets)
    assert reply.usage['input_count']['charge']['amount'] == '0.000123'
    assert reply.usage['context_budget']['limit'] == 131072
    assert admitted == ['count', 'generate']


@pytest.mark.parametrize('parameters', [
    {'reasoning': []}, {'reasoning': {'enabled': 1}},
    {'provider': {'order': 'Alibaba'}}, {'provider': {'allow_fallbacks': 'yes'}}])
def test_invalid_configuration_fails_before_network(parameters):
    with pytest.raises(ValueError):
        endpoint('openrouter', parameters=parameters)


def test_trace_displays_reasoning_details_without_duplicating_plain_text():
    from tools.eval_review_trial import reasoning_blocks
    got = list(reasoning_blocks(router_body()['choices'][0]['message']))
    assert got[0] == ('reasoning', 'readable state')
    assert len(got) == 2 and 'Opaque' in got[1][1]


def test_native_debit_settles_reserved_prices_and_reconciles_old_receipts(tmp_path, monkeypatch):
    from behave.agent.spending import SpendAccount, SpendPolicy

    ep = endpoint('openrouter')
    budget = SpendAccount(SpendPolicy(str(tmp_path / 'billing'), 'test', 'USD', '2', 131072))
    reply = response(ep, 200, canonical_json(router_body(call=False)))
    quote = ep.prices.reservation(100, 100)
    req = request(ep)
    original = SpendAccount._settlement
    with monkeypatch.context() as patcher:
        patcher.setattr(SpendAccount, '_settlement', staticmethod(lambda *args: {
            **original(*args), 'amount': None}))
        budget.dispatch(req, quote, lambda _: reply)
    before = budget.journal.load()
    assert budget.summary()['unpriced'] == 1
    assert budget.reconcile_receipts() == 1
    assert budget.reconcile_receipts() == 0
    assert budget.journal.load()[:len(before)] == before
    result = budget.summary()
    assert result['spent'] == '0.000123' and result['reserved'] == '0'
    assert result['unpriced'] == 0
    assert budget.dispatch(req, quote, lambda _: pytest.fail('must not call API again')) == reply
    cache = budget.run.directory / 'spending_cache.json'
    cache.rename(budget.run.directory / 'previous_spending_cache.json')
    cold = SpendAccount(budget.policy)
    assert cold.summary() == result


def test_native_charge_from_different_price_snapshot_stays_reserved(tmp_path):
    from behave.agent.spending import SpendAccount, SpendPolicy
    from behave.agent.model import Charge

    ep = endpoint('openrouter')
    budget = SpendAccount(SpendPolicy(str(tmp_path / 'billing'), 'test', 'USD', '2', 131072))
    reply = response(ep, 200, canonical_json(router_body(call=False)))
    budget.dispatch(request(ep), Charge('1', 'USD', 'another-snapshot'), lambda _: reply)
    assert budget.summary()['reserved'] == '1'
    assert budget.reconcile_receipts() == 0


def test_rollout_cost_includes_counting_receipt_without_an_extra_training_turn(tmp_path):
    from behave.agent import AttemptJournal, AttemptSpec, run_attempt
    from behave.agent.trajectory import trajectory
    from behave.core.storage import DirectoryStore

    ep = endpoint('openrouter')
    reply = response(ep, 200, canonical_json(router_body(call=False)))
    reply = replace(reply, usage={**thaw_json(reply.usage), 'input_count': {
        'charge': {'amount': '0.000040', 'currency': 'USD', 'pricing_revision': 'openrouter-usage-cost/1'}}})
    class Backend:
        def generate(self, request):
            return reply
    journal = AttemptJournal(tmp_path / 'events', DirectoryStore(tmp_path / 'objects'))
    run_attempt(journal=journal, spec=AttemptSpec('test', 'solver', ep.model_policy()),
        backend=Backend(), initial_messages=({'role': 'user', 'content': 'test'},), tools=())
    actual = trajectory(journal)
    assert len(actual.turns) == 1
    assert len(actual.charges) == 2
    assert Decimal(actual.cost_by_currency()['USD']) == Decimal('0.000163')
