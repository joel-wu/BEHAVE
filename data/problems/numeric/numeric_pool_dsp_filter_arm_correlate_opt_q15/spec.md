# Q15CorrelationWindow

Select one sample from the seven-element, zero-padded cross-correlation of a
four-element Q15 sequence A and a three-element Q15 sequence B. The selected
destination index preserves the full correlation's lag placement. Products
accumulate exactly in 64 bits, then shift and saturate to signed Q15.

## Interface

### RTL interface

```systemverilog
module q15_correlation_window (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [2:0]  in_lag,
    input  logic signed [15:0] in_a0,
    input  logic signed [15:0] in_a1,
    input  logic signed [15:0] in_a2,
    input  logic signed [15:0] in_a3,
    input  logic signed [15:0] in_b0,
    input  logic signed [15:0] in_b1,
    input  logic signed [15:0] in_b2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_corr
);
```

### Behavior interface

```python
class Q15CorrelationWindow:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_lag, in_a0, in_a1, in_a2, in_a3, in_b0, in_b1, in_b2): ...
```

`process(...)` returns one dict with key `out_corr`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_corr`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_correlation_window`: stateless correlation datapath with ordered
  ready/valid input and output channels.

## Functional Behavior

Interpret `in_a0..in_a3` and `in_b0..in_b2` as signed 16-bit integers A and B.
The unsigned 3-bit `in_lag=k` selects a destination index. For `0 <= k <= 6`,
the physical lag is `k-3` and the exact accumulator is

```text
acc[k] = sum(A[i]*B[j] for 0<=i<4, 0<=j<3, i-j=k-3)
```

Equivalently, the seven accumulators are:

```text
k=0: 0
k=1: A0*B2
k=2: A0*B1 + A1*B2
k=3: A0*B0 + A1*B1 + A2*B2
k=4: A1*B0 + A2*B1 + A3*B2
k=5: A2*B0 + A3*B1
k=6: A3*B0
```

Index 0 is the leading zero-padding position, not the first overlapping
window. Index 7 is outside the destination and returns zero. These rules
define all port bit patterns. The sum uses exact signed products with no
intermediate truncation or wrap. Compute `v=acc[k]>>15`, an arithmetic shift
rounding toward negative infinity, and return
`out_corr=min(32767,max(-32768,v))` as a 16-bit two's-complement pattern.

An input is accepted on a rising edge with `in_valid && in_ready`; it produces
exactly one ordered output. Input valid and payload remain stable until
acceptance. Output valid and payload remain stable while `out_ready` is low.
There is no cross-transaction numerical state and no prescribed latency.

## Reset Behavior

Reset is synchronous active-high. It clears `out_valid` and `out_corr`,
discards pending work, and makes the input channel available. No pre-reset
output may reappear after reset. Coefficients and sample history are not
retained because each command carries both complete input sequences.
