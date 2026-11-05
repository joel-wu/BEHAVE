# Correlate1DValid3

Correlate1DValid3 computes one 1-dimensional signed-integer correlation output sample for a fixed 3-tap kernel using the same operation order as a direct correlation kernel: a 3-element neighborhood from the input signal is multiplied elementwise by the kernel in forward kernel order and accumulated as a sum of three products. The block is stateful and memory-backed: each accepted command selects one valid-mode output index, the block reads three consecutive signal samples from external memory, forms the inner product with three latched kernel coefficients, and returns one signed 32-bit result.

## Interface

### RTL interface

```systemverilog
module correlate1d_valid3 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_base_addr,
    input  logic [15:0]  in_k0,
    input  logic [15:0]  in_k1,
    input  logic [15:0]  in_k2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_corr,

    output logic         signal_req_valid,
    output logic [7:0]   signal_req_addr,
    input  logic         signal_req_ready,
    input  logic         signal_resp_valid,
    input  logic [15:0]  signal_resp_rdata
);
```

### Behavior interface

```python
class Correlate1DValid3:
    def __init__(self, signal_init=None): ...
    def reset(self): ...
    def process(self, in_base_addr, in_k0, in_k1, in_k2): ...
```

`process(...)` returns a dict with key `out_corr`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_corr`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor signal_init supplies an optional standalone memory image, not an RTL
parameter. None creates 256 zero words; otherwise truncate to 256 words, mask each to
16 bits, and zero-pad shorter input. The environment binds the same configured image
for both executions.

self.signal is the external read-only view of 256 unsigned 16-bit words, interpreted
as signed samples by the computation. reset() preserves this view. Memory-request pins
are not return fields.

## Modules

- `correlate1d_valid3`: top-level single-output correlation kernel for one valid-mode 3-tap 1D correlation sample.

## Functional Behavior

Inputs `in_k0`, `in_k1`, and `in_k2` are 16-bit two's-complement signed integers. `in_base_addr` is an 8-bit unsigned address into the external read-only memory `signal`, whose elements are 16-bit two's-complement signed integers.

One accepted input transaction computes exactly one output transaction. The mathematical operation is:

- `x0 = signal[in_base_addr + 0]`
- `x1 = signal[in_base_addr + 1]`
- `x2 = signal[in_base_addr + 2]`

All address additions are performed modulo 256 on the memory address port. The three memory reads are therefore from unsigned 8-bit addresses `(in_base_addr + 0) & 0xFF`, `(in_base_addr + 1) & 0xFF`, and `(in_base_addr + 2) & 0xFF`.

Each `xN` is interpreted as a signed 16-bit integer. The kernel coefficients are interpreted as signed 16-bit integers. The output is the signed 32-bit two's-complement value of the sum

- `corr = (x0 * k0) + (x1 * k1) + (x2 * k2)`

where:
- each multiplication is signed 16-bit by signed 16-bit,
- each product is represented exactly in signed 32-bit range,
- the accumulation order is strictly left-to-right as written above,
- the final result is truncated to 32 bits by two's-complement wraparound.

Equivalently, `out_corr` is the 32-bit unsigned bit pattern corresponding to the signed 32-bit result of that wrapped sum.

This block implements correlation, not convolution: the kernel is not reversed. The sample at `signal[in_base_addr + 0]` multiplies `in_k0`, the sample at `signal[in_base_addr + 1]` multiplies `in_k1`, and the sample at `signal[in_base_addr + 2]` multiplies `in_k2`.

The transaction corresponds to a valid-mode 1D direct correlation window. No zero padding is applied inside the computation. Correct system use provides only base addresses for which the three-sample window lies within the intended signal extent. If a wrapped 8-bit address is presented, the block still performs the modulo-256 memory accesses defined above.

The external memory `signal` is read-only. For each accepted command, the RTL must issue exactly three read requests to `signal`, one for each address in increasing offset order `base+0`, `base+1`, `base+2`. The result must be computed from those three returned samples in the same order.

The read-only external memory `signal` has 256 signed 16-bit words,
whose image is supplied by the environment;
contents are not restricted to zero. A read request transfers when
`signal_req_valid && signal_req_ready`. The module holds its request valid and
address until accepted. Each request receives exactly one later response,
with `signal_resp_valid` and `signal_resp_rdata`, in request order; the earliest
response is the following rising edge. The environment may delay requests
and responses. No response-ready signal is used.

Inputs transfer on rising edges with `in_valid && in_ready`, and
outputs with `out_valid && out_ready`. Every accepted command produces exactly
one output, in command order. Both sides hold valid and payload stable while
stalled. Latency is implementation-dependent; accepted work must complete when
the environment continues servicing memory and outputs.

## Reset Behavior

Reset is synchronous active-high. It cancels pending work and outputs;
`in_ready=1`, `out_valid=0`, `out_corr=0`, and `signal_req_valid=0` after reset.
The environment cancels pending responses. The module does not clear external
memory. The environment supplies the configured memory image.
