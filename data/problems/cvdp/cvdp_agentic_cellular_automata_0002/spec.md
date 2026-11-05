# pseudoRandGenerator_ca

Evolve a seedable sixteen-cell cyclic automaton with Rule 30 or Rule 110.

## Interface

### RTL interface

```systemverilog
module pseudoRandGenerator_ca (
    input logic clock, init_reset, load_seed,
    input logic [15:0] CA_seed,
    input logic [1:0] rule_sel,
    output logic [15:0] CA_out
);
```

### Behavior interface

```python
class PseudoRandGeneratorCa:
    def __init__(self): ...
    def reset(self): ...
    def process(self, load_seed, CA_seed, rule_sel): ...
```

Each call returns exactly `{"CA_out": old_state}` immediately BEFORE one rising
edge of `clock`, then advances that edge. `CA_seed` and `old_state` are unsigned
16-bit integers, `load_seed` is in 0..1, and `rule_sel` is in 0..3. Every call
returns the complete dictionary, never `None` or a list; a call with
`load_seed=0` still evolves the automaton. State persists between calls.
Construction and the separate `reset()` call initialize it to zero; `reset()`
returns `None` and produces no output record. A nonzero seed is supplied through
`process(load_seed=1, CA_seed=seed, rule_sel=rule)`, not through `reset()`.

## Modules

- `pseudoRandGenerator_ca`: the complete native module above.

## Functional Behavior

The sixteen-bit state directly drives `CA_out`. On a rising edge,
`load_seed=1` loads `CA_seed` exactly, including zero; consecutive loads each take
effect. Otherwise compute ALL sixteen next bits from the same old state.
For cell i, left=state[(i+15)%16], center=state[i], right=state[(i+1)%16].
Thus cell 0's left neighbor is cell 15 and cell 15's right neighbor is cell 0.
The selector 01 uses Rule 110; 00, 10 and 11 use Rule 30. `CA_seed` has no effect
when load_seed=0. There is no enable, acceptance gate or output buffer.

| (left, center, right) | Rule 30 | Rule 110 |
| --- | ---: | ---: |
| 000 | 0 | 0 |
| 001 | 1 | 1 |
| 010 | 1 | 1 |
| 011 | 1 | 1 |
| 100 | 1 | 0 |
| 101 | 0 | 1 |
| 110 | 0 | 1 |
| 111 | 0 | 0 |

### Explicit reset/load interface adaptation

The source's data-bearing synchronous `reset` is represented by the ordinary
input `load_seed`, so its nonzero seed operation remains observable and is NOT
replaced by a zero reset. A separate synchronous high `init_reset` initializes
the state to zero for the task boundary; it has priority over load_seed. On every
edge with init_reset=0, substituting original reset=load_seed preserves the source
transition exactly. This changes the interface, not the local CA rules. No
cryptographic-strength or maximal-period guarantee is required.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clock` with reset asserted restores the initial state. All stored state, including memory and pipeline registers when present, is zero.
After reset release, resume the edge-by-edge behavior above.
