"""State audits observe logical values, regardless of dense/compact backing."""
import pytest

from behave.sim.behavior.source_values import SCALAR, ValueContract
from behave.sim.symbolic.state_projection import (
    DynamicSequenceBinding,
    SourceProductBinding,
    StateProjectionError,
)
from behave.sim.symbolic.uniform_array import UniformArray


def test_nested_dynamic_projection_reads_compact_data_and_lengths(monkeypatch):
    row = ValueContract.sequence('tuple', 0, 3, SCALAR)
    contract = ValueContract.sequence('list', 0, 2, row)
    binding = DynamicSequenceBinding('rows', 'data', contract,
                                     ((0, 'count'), (1, 'lengths')))
    state = {'data': UniformArray.uniform(2, UniformArray.uniform(3, 9))
             .store(0, UniformArray.uniform(3, 4).store(1, 5)),
             'count': 2, 'lengths': UniformArray.uniform(2, 0).store(0, 2)}

    def no_expansion(_self):
        pytest.fail('projection must not expand inactive backing cells')

    monkeypatch.setattr(UniformArray, 'materialize', no_expansion)
    assert binding.reconstruct(state) == [(4, 5), ()]
    with pytest.raises(StateProjectionError, match='outside its contract'):
        binding.reconstruct({**state, 'lengths': UniformArray.uniform(2, 4)})
    with pytest.raises(StateProjectionError, match='wrong shape'):
        binding.reconstruct({**state, 'data': UniformArray.uniform(1, [4, 5, 9])})


def test_product_projection_preserves_tuple_and_boolean_carriers():
    row = ValueContract.sequence('tuple', 2, 2, SCALAR)
    contract = ValueContract.record({'flags': row})
    binding = SourceProductBinding('status', contract, ((('flags',), 'flags'),))
    result = binding.reconstruct({'flags': UniformArray.uniform(2, False)
                                  .store(1, True)})
    assert result == {'flags': (False, True)}
    assert all(type(value) is bool for value in result['flags'])
    with pytest.raises(StateProjectionError, match='wrong shape'):
        binding.reconstruct({'flags': UniformArray.uniform(3, False)})
