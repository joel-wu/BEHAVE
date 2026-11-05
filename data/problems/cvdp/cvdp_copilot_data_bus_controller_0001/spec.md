# data_bus_controller

Native two-master static-affinity bus with one registered output stage.

## Interface

### RTL interface

```systemverilog
module data_bus_controller #(parameter int AFINITY=0) (
    input logic clk, rst_n,
    input logic m0_valid,
    input logic [31:0] m0_data,
    input logic m1_valid,
    input logic [31:0] m1_data,
    input logic s_ready,
    output logic m0_ready, m1_ready, s_valid,
    output logic [31:0] s_data
);
```

### Behavior interface

```python
class DataBusController:
    def __init__(self, AFINITY=0): ...
    def reset(self): ...
    def process(self, m0_valid, m0_data, m1_valid, m1_data, s_ready): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `m0_ready`, `m1_ready`, `s_valid`: 1.
- `s_data`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `data_bus_controller`: the complete native module above.

## Functional Behavior

AFINITY is 0 or 1, default 0. The two masters are independent
ready/valid interfaces. Use the source port-table/test spellings m0_ready,
m1_ready and s_ready; the header's truncated *_read names are corrected.

Choose the only valid master, or AFINITY if both are valid. Only the chosen
master's ready can assert, and it equals s_ready when reset is released;
with neither master valid, both readies are zero. Transfer occurs on that
master's valid AND ready. The source's 'ignored' loser means NOT granted,
not acknowledged and silently discarded; a compliant sender retains its
request until granted. No request-age queue or round-robin fairness is added.

One output register presents the accepted word and s_valid immediately after
the acceptance edge; another input transaction is not needed to expose it.
When s_ready=0 hold the output and accept nothing, even if the register is
empty. This preserves the source's explicit slave-ready dependence. When
s_ready=1 retire the old output and simultaneously capture the selected input,
or clear s_valid/data if neither input is valid. Accepted requests retain order
and sustain one word per clock. s_data=0 when invalid. Reset clears both
outputs and forces master readies low.


## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
