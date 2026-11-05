# PidStateResetF32

Clear the three 32-bit state words of a CMSIS-DSP PID instance in external memory. This is the state-reset operation of `arm_pid_reset_f32`, not the PID control equation. Completion alone is insufficient: the three addressed words must become zero and all other words must remain unchanged.

## Interface

### RTL interface

```systemverilog
module pid_state_reset_f32 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [3:0] in_base,
    output logic out_valid,
    input logic out_ready,
    output logic out_done,
    output logic mem_req_valid,
    output logic mem_req_we,
    output logic [3:0] mem_req_addr,
    output logic [31:0] mem_req_wdata,
    input logic mem_req_ready,
    input logic mem_resp_valid,
    input logic [31:0] mem_resp_rdata
);
```

`mem` is a 16-word read-write external memory with 32-bit words and word addresses. Initial contents are supplied by the environment and need not be zero. A request transfers when `mem_req_valid && mem_req_ready`; `mem_req_we=1` writes the complete word. Hold a stalled request's address, direction, and data stable. This kernel issues writes only and ignores the read-response inputs.

### Behavior interface

```python
class PidStateResetF32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_base): ...
```

The environment binds `self.mem` to the same external memory. One input transaction supplies an unsigned four-bit base word address; one output transaction returns `out_done`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_done`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory view is self.mem: 16 unsigned 32-bit words. Completion is the
dictionary below; memory-request pins are not return fields. The environment binds and
preserves external memory across reset.

### Transaction protocol

Input and output use ready/valid, with outputs held stable until accepted.

## Modules

- `pid_state_reset_f32`: bounded three-word memory-clear controller.
- `mem`: externally owned storage containing the selected PID state and unrelated words.

## Functional Behavior

For `0 <= in_base <= 13`, write the 32-bit pattern `0x00000000` to `mem[in_base]`, `mem[in_base+1]`, and `mem[in_base+2]`, in that order, then emit `out_done=1`. Completion may not precede the third accepted write. All other memory words are untouched, including any coefficient or neighboring-instance words. Repeated or overlapping operations apply to current memory contents in input order.

Bases 14 and 15 cannot contain three complete words: perform no memory access and emit `out_done=0`. This defined no-op error result completes the finite input domain; it is not an upstream C pointer rule.

The source performs `memset(S->state, 0, 3*sizeof(float32_t))`. Mapping the state pointer to a bounded word address and adding hardware transport are adaptations. Values are raw IEEE-754 binary32 bit patterns, but no floating-point arithmetic is performed: clearing positive or negative numbers, infinities, NaNs, or negative zero always produces positive-zero bits.

## Reset Behavior

`rst` is synchronous active high. It discards pending completion and cancels the unfinished controller operation. No memory request is asserted while reset is high. External memory is retained: already accepted writes are not rolled back, and reset alone does not clear PID state. A new command must be issued after reset to clear it.
