# FP8 Amax History Rotation

Advance the four-slot history used by delayed FP8 scaling, then clear the new current-epoch slot. History is explicit input/output data, not hidden state.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_fp8_fp8_amax_history (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_h0,
    input  logic [31:0] in_h1,
    input  logic [31:0] in_h2,
    input  logic [31:0] in_h3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_h0,
    output logic [31:0] out_h1,
    output logic [31:0] out_h2,
    output logic [31:0] out_h3
);
```

### Behavior interface

```python
class AiMlQuantFp8Fp8AmaxHistory:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_h0, in_h1, in_h2, in_h3): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_h0": ..., "out_h1": ..., "out_h2": ..., "out_h3": ...}`. Arguments
and returned fields are Python integers holding the specified opaque port
bit patterns, not Python floating-point values or a list of history entries.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

Input h0 is the completed current-epoch amax; h1 is the oldest slot to discard, followed by h2 and h3. Apply a one-slot left rotation `[h1,h2,h3,h0]` and then replace element zero with positive floating zero. Thus

```
(out_h0, out_h1, out_h2, out_h3) = (0, in_h2, in_h3, in_h0)
```

Each field is an opaque 32-bit binary32 word: retained words are copied bitwise, including signed zeros, infinities and NaN payloads. The discarded h1 cannot affect the output. The caller owns history feedback and writes the next epoch's observed amax into h0 before the next update. This operation neither computes amax nor computes a scaling factor. Every input history is independent; there is no implicit recurrence across transactions.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
