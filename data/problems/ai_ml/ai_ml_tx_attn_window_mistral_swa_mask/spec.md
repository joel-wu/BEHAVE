# Mistral Sliding-Window Causal Predicate

Evaluate visibility of one key position for one query, without constructing an attention tensor.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_window_mistral_swa_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_query_position,
    input  logic [15:0]  in_key_position,
    input  logic [15:0]  in_sliding_window,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_allow
);
```

### Behavior interface

```python
class AiMlTxAttnWindowMistralSwaMask:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_query_position, in_key_position, in_sliding_window): ...
```

`process(...)` returns:
- `{"out_allow": ...}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_allow`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

All inputs are unsigned 16-bit. With `q=in_query_position`, `k=in_key_position`, `w=in_sliding_window`, return 1 exactly when `k<=q` and `q-k<w`, else 0. Subtraction is mathematical, not unsigned wrapping. The visible integer interval is `[max(0,q-w+1),q]`; its endpoints are inclusive. For w=0 it is empty. Distance w is excluded; distance w-1 is included when w>0. There is no padding-mask input, cache offset inference or softmax. The window includes the query itself and at most w-1 earlier keys.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
