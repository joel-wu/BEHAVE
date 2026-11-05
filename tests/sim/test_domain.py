"""A trace domain remains the record its digest names."""

import hashlib
import json
from collections.abc import Mapping

import pytest

from behave.sim.core.domain import TraceDomain


def _domain(**overrides):
    values = {
        "bound": 3,
        "contract": {"a": [0, 7]},
        "initial": {"tree": [[1, 2], {"leaf": [3]}]},
        "schema": {"out": {"fields": ["y"]}},
        "frontend": "test/1",
        "policy": "source-site/1",
        "artifact": "behavior.py",
        "task_contract": "task-contract",
    }
    values.update(overrides)
    return TraceDomain(**values)


def test_constructor_inputs_cannot_mutate_the_domain_snapshot():
    contract = {"a": [0, 7]}
    initial = {"tree": [[1, 2], {"leaf": [3]}]}
    domain = _domain(contract=contract, initial=initial)
    before = domain.as_dict()
    digest = domain.digest()

    contract["a"][1] = 99
    initial["tree"][0][0] = 99
    initial["tree"][1]["leaf"].append(99)

    assert domain.as_dict() == before
    assert domain.digest() == digest


def test_public_views_are_deep_copies_not_backdoors_into_the_domain():
    domain = _domain()
    before = domain.as_dict()
    digest = domain.digest()

    whole = domain.as_dict()
    whole["initial"]["tree"][0][0] = 99
    whole["contract"]["a"][1] = 99
    initial = domain.initial
    initial["tree"][1]["leaf"].append(99)
    contract = domain.contract
    contract["a"][0] = -99

    assert domain.as_dict() == before
    assert domain.initial is not initial
    assert domain.contract is not contract
    assert domain.digest() == digest


def test_the_record_itself_cannot_be_reassigned_after_construction():
    domain = _domain()
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        domain._canonical = "{}"
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        domain.bound = 9


def test_digest_is_sha256_of_canonical_json_and_order_is_canonical():
    body = _domain().as_dict()
    expected = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()).hexdigest()
    reordered = TraceDomain(
        bound=body["bound"],
        contract={"a": [0, 7]},
        initial={"tree": ((1, 2), {"leaf": (3,)})},
        schema={"out": {"fields": ("y",)}},
        frontend=body["frontend"], policy=body["policy"],
        artifact=body["artifact"], task_contract=body["task_contract"])

    assert _domain().digest() == expected
    assert len(_domain().digest()) == 64
    # Tuples and lists are the same JSON array carrier, and mapping insertion
    # order is not part of a trace domain's identity.
    assert reordered.same_as(_domain())


@pytest.mark.parametrize("value", [
    object(),
    {1, 2},
    b"bytes",
])
def test_non_json_values_are_refused_instead_of_stringified(value):
    with pytest.raises(TypeError, match="no canonical JSON image"):
        _domain(initial={"bad": value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_values_are_refused(value):
    with pytest.raises(ValueError, match="non-finite float"):
        _domain(initial={"bad": [value]})


def test_resource_limits_are_not_part_of_a_trace_domain_identity():
    domain = _domain()

    assert "budgets" not in domain.as_dict()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        TraceDomain(**domain.as_dict(), budgets={"query_ms": 1})


@pytest.mark.parametrize("bound", [0, -1, True, None])
def test_a_bounded_trace_domain_requires_a_positive_integer_horizon(bound):
    with pytest.raises(ValueError, match="positive integer horizon"):
        _domain(bound=bound)


@pytest.mark.parametrize("identity", ["", 1, True, []])
def test_task_contract_identity_is_typed(identity):
    with pytest.raises(ValueError, match="task-contract identity"):
        _domain(task_contract=identity)


def test_mapping_keys_cannot_be_silently_coerced_or_collide():
    with pytest.raises(TypeError, match="non-string mapping key"):
        _domain(initial={"bad": {1: "integer", "1": "string"}})


def test_cycles_are_refused_as_non_finite_snapshots():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="reference cycle"):
        _domain(initial={"bad": cycle})


def test_domain_construction_does_not_execute_an_arbitrary_mapping_adapter():
    class HostileMapping(Mapping):
        def __getitem__(self, _key):
            raise AssertionError("candidate mapping code ran")

        def __iter__(self):
            raise AssertionError("candidate mapping code ran")

        def __len__(self):
            raise AssertionError("candidate mapping code ran")

    with pytest.raises(TypeError, match="exact built-in mapping"):
        _domain(contract=HostileMapping())
