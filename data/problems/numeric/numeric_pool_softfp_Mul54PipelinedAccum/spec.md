# Mul54 logical pipeline step

Advance a 54-by-54 unsigned multiply-add pipeline by one logical tick. Operand
latches can be reused independently; the addend is supplied at stage 2, not
captured with the original operands. This is a transaction-stepped version of
the HardFloat Mul54 datapath, not a floating-point operator or its native clock
interface.

## Interface

### RTL interface

```systemverilog
module Mul54PipelinedAccum (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_val_s0,
    input  logic         in_latch_a_s0,
    input  logic [53:0]  in_a_s0,
    input  logic         in_latch_b_s0,
    input  logic [53:0]  in_b_s0,
    input  logic [104:0] in_c_s2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [104:0] out_result_s3
);
```

All data ports are unsigned raw words. `in_val_s0` is the logical stage-0
valid bit, distinct from the enclosing transport's `in_valid`.

### Behavior interface

```python
class Mul54PipelinedAccum:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_val_s0, in_latch_a_s0, in_a_s0,
                in_latch_b_s0, in_b_s0, in_c_s2): ...
```

A call returns either None (no output transaction) or one mapping containing
`out_result_s3`.

Each call advances one accepted logical tick. Return one dictionary exactly when the
old V2 is one (the in_val_s0 bit accepted two calls earlier), otherwise None. Never
return a list. Even a tick with in_val_s0=0 advances the pipeline. Reset discards all
in-flight ticks.
Every returned dictionary contains exactly these fields (bit widths):

- `out_result_s3`: 105.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

`Mul54PipelinedAccum` implements the staged multiplier and an elastic result
buffer.

## Functional Behavior

State consists of logical valid bits V1,V2 and 54-bit operand pairs (A1,B1)
and (A2,B2). Every accepted input advances one logical tick. All right-hand
sides below use old state, with simultaneous updates:

1. Set V1=in_val_s0 and V2=old V1.
2. If in_val_s0=1, load A1=in_a_s0 only when in_latch_a_s0=1, and load
   B1=in_b_s0 only when in_latch_b_s0=1. Otherwise each latch retains its value.
3. If old V1=1, copy (A1,B1) into (A2,B2); otherwise that pair holds.
4. If old V2=1, emit (A2*B2+in_c_s2) mod 2^105. Otherwise emit nothing.

A request with logical in_val_s0=1 therefore leads to a result on the second
subsequent accepted request (the third tick counting that request). That later
request supplies the addend. Two logical bubble requests, with in_val_s0=0,
flush all earlier operand requests. Physical idle cycles do not flush or advance
the pipeline. No implicit end-of-run outputs are emitted.

### Transport

Records transfer on rising edges with valid && ready. Input ready may deassert
under output backpressure. A stalled response retains valid and its result;
the logical pipeline cannot advance in a way that overwrites it. All responses
remain ordered. Payload is zero while out_valid=0. Logical bubbles may still
produce earlier results. No fixed physical-clock latency is required.

## Reset Behavior

Synchronous active-high reset clears both operand pairs, both logical valid
bits, and any pending result. It cancels all in-flight computations and returns
in_ready=1, out_valid=0 and out_result_s3=0. The original uninitialized datapath
registers are explicitly zero-initialized in this task.
