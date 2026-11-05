# AI/ML Architecture Crossbar Round-Robin Arbiter

This module accepts a packed set of `N` input requests and forwards at most one selected payload to its output. Selection uses round-robin priority, so consecutive accepted request sets begin searching immediately after the most recently selected lane. The selected payload is forwarded unchanged.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_crossbar_round_robin_arbiter #(
    parameter int N = 4,
    parameter int DATA_W = 32,
    parameter int INDEX_W = (N <= 1) ? 1 : $clog2(N)
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [N-1:0]             in_req,
    input  logic [N*DATA_W-1:0]      in_data,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [DATA_W-1:0]        out_data,
    output logic [INDEX_W-1:0]       out_index
);
```

Supported configurations have `N >= 1`, `DATA_W >= 1`, and the default `INDEX_W` shown above. The benchmark instance uses `N = 4` and `DATA_W = 32`.

Lane `i` is requested when `in_req[i] = 1` and carries payload `in_data[i*DATA_W +: DATA_W]`.

### Behavior interface

```python
class AiMlArchCrossbarRoundRobinArbiter:
    def __init__(self, N=4, DATA_W=32):
        pass

    def reset(self):
        pass

    def process(self, in_req, in_data):
        pass
```

Each `process(in_req, in_data)` call represents one accepted request-set
transaction. Both arguments are unsigned integers with the declared port
widths: `N` bits for `in_req` and `N*DATA_W` bits for `in_data`.
When `in_req` is zero, return `None` and preserve the arbitration state.
Otherwise return one dictionary, not a list, with exactly these keys:

```python
{"out_data": ..., "out_index": ...}
```

`out_data` is an unsigned `DATA_W`-bit integer and `out_index` is an integer
from `0` through `N-1`. A selected zero payload still produces a dictionary.
The last selected lane persists between calls. `reset()` restores it to
`N-1` and returns `None`, without producing a response.

## Modules

- `ai_ml_arch_crossbar_round_robin_arbiter`: top-level non-locking round-robin arbiter.

## Functional Behavior

The arbiter stores `last_grant`, the lane selected by the most recent nonempty transaction. For a nonzero request mask, it probes lanes in this order:

1. `(last_grant + 1) mod N`,
2. `(last_grant + 2) mod N`,
3. through `(last_grant + N) mod N`.

The first requested lane in this order is `chosen`. The output transaction contains

- `out_index = chosen`, and
- `out_data = in_data[chosen*DATA_W +: DATA_W]`.

The input and output use separate ready/valid handshakes. A request set is accepted when `in_valid && in_ready` is true at a rising clock edge; an output transfers when `out_valid && out_ready` is true at a rising clock edge. Each accepted nonempty request set produces exactly one output in input acceptance order. Input and output transfers need not occur in the same cycle; no fixed latency or throughput is required. Unless reset intervenes, each accepted nonempty request set must eventually produce its output when output backpressure is released. Outside reset, the source holds `in_valid`, `in_req`, and `in_data` stable while `in_valid && !in_ready`, and the module holds `out_valid`, `out_data`, and `out_index` stable while `out_valid && !out_ready`.

An accepted transaction whose request mask is zero is a no-op: it emits no output and does not change `last_grant`. This makes the interface total over every `N`-bit request mask without inventing a selected lane.

The input is a **request-set snapshot**, not `N` independently handshaken
channels. Acceptance consumes that snapshot; unselected lanes are not queued
or implicitly accepted as separate requests. To request service again, the
source must include them in a subsequent snapshot. This task uses rotating
priority without multi-transfer locking; buffering selected outputs does not
queue unselected lanes for later service.

Arbitration follows input acceptance order. Each nonempty accepted request set updates the logical `last_grant` to `chosen` for the next request set. Implementations may buffer or pipeline computation while preserving this sequence.

Payloads are treated as unsigned bit patterns and forwarded without arithmetic or decoding. When `out_valid = 0`, `out_data` and `out_index` are don't-care.

## Reset Behavior

Reset is synchronous active-high and takes precedence over transfers. No request or response is counted on a reset edge. On reset, `last_grant` is set to `N - 1`, so the first nonempty transaction gives lane `0` highest priority. Any pending outputs from previously accepted request sets are discarded.
