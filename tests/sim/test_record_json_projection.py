"""The faster JSON projection must retain the freeze/thaw carrier semantics."""
import json
import random

import pytest

from behave.sim.core.frozen import FrozenMap
from behave.sim.core.record_value import (
    FrozenValueMap, canonical_json, freeze_record_value, thaw_record_value,
)

def old_projection(value):
    return json.dumps(thaw_record_value(freeze_record_value(value)),
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)

def test_generated_carriers_have_identical_canonical_bytes():
    rng = random.Random(2132)
    atoms = [None, False, True, 0, 1, -3, 2**257, 0.0, -0.0, 0.125, '', '汉字', '\n"\\']
    def value(depth):
        if not depth or rng.randrange(3) == 0: return rng.choice(atoms)
        if rng.randrange(2): return {f'k{i}': value(depth-1) for i in range(rng.randrange(5))}
        sequence = [value(depth-1) for _ in range(rng.randrange(5))]
        return sequence if rng.randrange(2) else tuple(sequence)
    for item in atoms + [value(5) for _ in range(300)]:
        expected = old_projection(item)
        assert canonical_json(item) == expected
        assert canonical_json(freeze_record_value(item)) == expected
    assert canonical_json(FrozenMap({'b': 2, 'a': (1, 3)})) == '{"a":[1,3],"b":2}'

@pytest.mark.parametrize('item', [float('nan'), float('inf'), {1: 'not-a-string-key'},
    {1, 2}, bytearray(b'1'), object(),
    FrozenValueMap((('duplicate', 1), ('duplicate', 2))),
    FrozenValueMap(((1, 'wrong-key-type'),))])
def test_invalid_carriers_fail_identically(item):
    with pytest.raises((ValueError, TypeError)) as old: old_projection(item)
    with pytest.raises(type(old.value)) as new: canonical_json(item)
    assert str(new.value) == str(old.value)

def test_cycles_subclasses_and_mutable_frozen_map_payload_are_revalidated():
    class Integer(int): pass
    class Mapping(dict): pass
    cyclic = []; cyclic.append(cyclic)
    payload = [1]
    unsealed = FrozenValueMap((('data', payload),))
    assert canonical_json(unsealed) == '{"data":[1]}'
    payload.append(2)
    assert canonical_json(unsealed) == '{"data":[1,2]}'
    for item in (Integer(1), Mapping(a=1), cyclic):
        with pytest.raises((ValueError, TypeError)) as old: old_projection(item)
        with pytest.raises(type(old.value)) as new: canonical_json(item)
        assert str(new.value) == str(old.value)

def test_pair_iteration_does_not_rescan_frozen_keys(monkeypatch):
    frozen = freeze_record_value({str(i): {'word': i} for i in range(1024)})
    def forbidden_lookup(self, key):
        raise AssertionError('serialization must walk pairs, not repeat linear key lookup')
    monkeypatch.setattr(FrozenValueMap, '__getitem__', forbidden_lookup)
    assert json.loads(canonical_json(frozen))['1023'] == {'word': 1023}
    assert thaw_record_value(frozen)['0'] == {'word': 0}
    assert len(freeze_record_value(frozen)) == 1024
