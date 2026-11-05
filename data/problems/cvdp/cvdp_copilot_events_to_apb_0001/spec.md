# apb_controller

Event-priority APB write master with native bus pins and a bounded access timeout.

## Interface

### RTL interface

```systemverilog
module apb_controller (
    input logic clk, reset_n, select_a_i, select_b_i, select_c_i,
    input logic [31:0] addr_a_i, data_a_i, addr_b_i, data_b_i, addr_c_i, data_c_i,
    input logic apb_pready_i,
    output logic apb_psel_o, apb_penable_o, apb_pwrite_o,
    output logic [31:0] apb_paddr_o, apb_pwdata_o
);
```

### Behavior interface

```python
class ApbController:
    def __init__(self): ...
    def reset(self): ...
    def process(self, select_a_i, select_b_i, select_c_i, addr_a_i, data_a_i, addr_b_i, data_b_i, addr_c_i, data_c_i, apb_pready_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `apb_paddr_o`, `apb_pwdata_o`: 32.
- `apb_penable_o`, `apb_psel_o`, `apb_pwrite_o`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `apb_controller`: the complete native module above.

## Functional Behavior

Native APB write master with IDLE, SETUP and ACCESS phases. IDLE samples
event selects on each rising edge, choosing A before B before C, and captures
that event's entire 32-bit address and data. The following cycle is SETUP with
psel=pwrite=1, penable=0. The next edge enters ACCESS, adding penable=1; address,
data, select and write remain stable for every wait cycle. An ACCESS edge with
apb_pready_i=1 completes the transfer and returns to IDLE. Otherwise the 15th
consecutive ACCESS edge with ready low aborts and returns to IDLE. Ready wins
on that last edge. Count ACCESS edges only, not SETUP. IDLE drives every output
zero. Timeout state resets after completion, abort or reset.

Events in SETUP/ACCESS are ignored; there is no queue. The source's valid
environment emits event pulses only in IDLE, but this completion also defines
busy-time pulses without inventing new work. A held event select can start
again when IDLE is reached. The first accepting edge e produces SETUP after e,
ACCESS after e+1, and earliest completion after e+2. This is the source's
three-edge sequence. No artificial in_valid/out_ready clock gating is added.
The whole APB output bundle, including write address and content, is observed.

## Reset Behavior

`reset_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
