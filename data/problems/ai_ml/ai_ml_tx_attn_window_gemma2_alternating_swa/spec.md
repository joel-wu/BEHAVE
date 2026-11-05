# Gemma2 Alternating Sliding-Window Selector

Select the default Gemma2 layer mode from a zero-based layer index: even layers use sliding attention and odd layers use full attention.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_window_gemma2_alternating_swa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_layer_idx,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_is_sliding_attention
);
```

### Behavior interface

```python
class Gemma2AlternatingSlidingWindowSelector:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_layer_idx): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_is_sliding_attention": ...}`, whose value is the Python integer `0`
or `1`, not a bare scalar or boolean.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

`in_layer_idx` is unsigned 32-bit. Return `out_is_sliding_attention=1` for even indices and 0 for odd indices. This is the default alternating schedule, not an arbitrary `layer_types` configuration. The two-entry pattern repeats over every representable index; this standalone selector does not validate a model's layer count or construct attention masks.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
