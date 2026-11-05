# Modulo-12 Enabled Counter

Count modulo 12 on rising clock edges when `valid_count` is high. There is no transaction handshake.

## Interface

### RTL interface

```systemverilog
module counter_12 (
    input logic clk,
    input logic rst_n,
    input logic valid_count,
    output logic [3:0] out
);
```

### Behavior interface

```python
class Modulo12Counter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, valid_count): ...
```

One call represents one non-reset rising edge. It returns exactly
`{"out": old_count}`, a dictionary whose sole value is the unsigned four-bit
count immediately before that edge, then applies the one-bit `valid_count` to
the state. Disabled edges still return a dictionary, not `None`. Thus the first
observation is zero; twelve enabled edges wrap the state to zero, observed by
the next call. Keep the same instance between edges. `reset()` returns `None`
and restores count zero; it is not an output-producing clock step.

## Modules

- `counter_12`: modulo-12 enabled counter.

## Functional Behavior

The count starts at zero. At each rising edge, `valid_count = 1` advances it through `0,1,...,11,0`; `valid_count = 0` holds the count. The unsigned four-bit `out` continuously exposes the stored count. Values 12 through 15 never occur in a reset-started execution.

`valid_count` is a one-bit control sampled on every edge, not a valid qualifier for the output. Disabled edges still have an observable count.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately clears the count and `out` to zero.
