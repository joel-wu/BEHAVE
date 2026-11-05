# cvdp_copilot_decode_firstbit

Locate the lowest asserted input bit with configurable input, internal and output register stages, propagating validity through the selected pipeline.

## Interface

### RTL interface

```systemverilog
module cvdp_copilot_decode_firstbit #(
    parameter int InWidth_g = 32,
    parameter int InReg_g = 1,
    parameter int OutReg_g = 1,
    parameter int PlRegs_g = 1
) (
    input logic Clk, Rst,
    input logic [InWidth_g-1:0] In_Data,
    input logic In_Valid,
    output logic [(InWidth_g>1 ? $clog2(InWidth_g) : 1)-1:0] Out_FirstBit,
    output logic Out_Found, Out_Valid
);
```

### Behavior interface

```python
class DecodeFirstbit:
    def __init__(self, InWidth_g=32, InReg_g=1, OutReg_g=1, PlRegs_g=1): ...
    def reset(self): ...
    def process(self, In_Data, In_Valid): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `Out_FirstBit`: (InWidth_g > 1 ? $clog2(InWidth_g) : 1).
- `Out_Found`, `Out_Valid`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `Clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `cvdp_copilot_decode_firstbit`: the complete native module with the ports and parameters above.

## Functional Behavior

`InWidth_g >= 1`; `InReg_g` and `OutReg_g` are each 0 or 1.
Let L = ceil(log2(InWidth_g)). The legal internal-register count is
`0 <= PlRegs_g <= max(0,floor(L/2)-1)`. The index port has `max(1,L)` bits.
Zero inputs produce index 0 and found 0; otherwise return the lowest numbered
set bit, with bit 0 least significant. Non-power-of-two inputs are padded with
zeroes, never with searchable bits.

An enabled input register stores data and valid before decoding. Internal
registers divide the priority-reduction logic into successive stages; an enabled
output register stores the decoded index, found and valid. The total number of
register stages is exactly `K = InReg_g + PlRegs_g + OutReg_g`. In pre-edge
observations, a record sampled at edge n appears at edge n+K. For K=0 the
output is combinational in the current inputs: no register is inserted.

Accept a new data/valid pair every edge without backpressure. Valid propagates
independently of the data: invalid records are bubbles, not clock enables.
Their index/found bits still decode their own data. Reset initializes every
pipeline entry to zero and invalid. All bypass configurations also force zero
outputs while reset is asserted.

## Reset Behavior

`Rst` is asynchronous and active high. Assertion immediately clears all state, pending work and output pins to zero.
