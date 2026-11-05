# RWKV6 Streaming Token-Shift Difference

Retain the previous accepted token vector and subtract the current vector lane by lane. This instantiates the source time-shift difference in a modular integer domain.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_rwkv_rwkv6_token_shift #(
    parameter int HIDDEN_SIZE = 1,
    parameter int DATA_WIDTH  = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [HIDDEN_SIZE*DATA_WIDTH-1:0]  in_hidden_states,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [HIDDEN_SIZE*DATA_WIDTH-1:0]  out_delta
);
```

`in_hidden_states` and `out_delta` are packed vectors containing `HIDDEN_SIZE` lanes, each `DATA_WIDTH` bits wide. Lane `i` occupies bits `[i*DATA_WIDTH +: DATA_WIDTH]`.

### Behavior interface

```python
class RWKV6TokenShift:
    def __init__(self, hidden_size=1, data_width=32):
        pass

    def reset(self):
        pass

    def process(self, in_hidden_states):
        pass
```

`process()` returns:
- `{"out_delta": packed_vector}` for every accepted input transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_delta`: HIDDEN_SIZE*DATA_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor hidden_size and data_width match RTL HIDDEN_SIZE and DATA_WIDTH.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

`HIDDEN_SIZE` and `DATA_WIDTH` are positive. Lane i occupies bits `[i*DATA_WIDTH +: DATA_WIDTH]`. For every accepted input, compute `delta[i]=(previous[i]-current[i]) mod 2**DATA_WIDTH`, pack the lanes identically, and then store the complete current vector as previous. There is no cross-lane borrow. No float-bit subtraction is intended: these are modular integer lanes, not IEEE encodings. Output backpressure and idle cycles never advance previous state. Reset alone marks a new sequence; this task has no batch/padding or intra-stream boundary flags.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. The previous vector is reset to all zeros. Thus the first post-reset result is the lane-wise negation of its input. Output payload is zero whenever output valid is low.
