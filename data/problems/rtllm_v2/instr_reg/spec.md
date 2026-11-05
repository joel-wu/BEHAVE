# Instruction Register

Capture two independent instruction bytes and expose their fields on native continuously driven outputs. Inputs are sampled on every rising edge, without ready/valid.

## Interface

### RTL interface

```systemverilog
module instr_reg (
    input logic clk,
    input logic rst,
    input logic [1:0] fetch,
    input logic [7:0] data,
    output logic [2:0] ins,
    output logic [4:0] ad1,
    output logic [7:0] ad2
);
```

### Behavior interface

```python
class InstructionRegister:
    def __init__(self): ...
    def reset(self): ...
    def process(self, fetch, data): ...
```

A call returns the pre-edge mapping `{"ins": ..., "ad1": ..., "ad2": ...}` and then updates the selected register. No-update selectors also produce observations; they are not omitted from the input stream.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ad1`: 5.
- `ad2`: 8.
- `ins`: 3.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `instr_reg`: instruction register.

## Functional Behavior

`fetch` is unsigned two-bit data and `data` is an unsigned byte. A rising edge with `fetch = 1` stores `data` in register p1; `fetch = 2` stores it in p2. Values 0 and 3 preserve both registers. Writing either register leaves the other unchanged.

The outputs continuously expose `ins = p1[7:5]`, `ad1 = p1[4:0]`, and `ad2 = p2`, of widths 3, 5 and 8 respectively. The newly written fields are visible after the capturing edge.

## Reset Behavior

`rst` is asynchronous and **active low**, despite its name. Assertion clears both instruction registers and all three output fields, as in the original RTL and TB.
