# Strided complex integer conjugate dot product

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module complex_conjugate_dot_product (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [2:0] in_n,
    input logic [3:0] in_incx, in_incy, in_x_base, in_y_base,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_real, out_imag,
    output logic x_mem_req_valid,
    output logic [3:0] x_mem_req_addr,
    input logic x_mem_req_ready, x_mem_resp_valid,
    input logic [127:0] x_mem_resp_rdata,
    output logic y_mem_req_valid,
    output logic [3:0] y_mem_req_addr,
    input logic y_mem_req_ready, y_mem_resp_valid,
    input logic [127:0] y_mem_resp_rdata
);
```

### Behavior interface

```python
class ComplexConjugateDotProduct:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_n, in_incx, in_incy, in_x_base, in_y_base): ...
```

Each call represents one accepted command and returns exactly
`{"out_real": real_bits, "out_imag": imag_bits}`. Arguments use unsigned port
encodings: `in_n` is 3 bits, and strides/bases are 4 bits. Both results are
unsigned 128-bit integers encoding the wrapped signed sums, not Python complex
numbers, tuples or lists. `n=0` returns both zero fields without memory reads,
never `None`. The return dictionary contains no handshake or memory-bus pins.
`x_mem` and `y_mem` are externally populated lists of 16 unsigned 128-bit words
using the packing below. Construction and `reset()` recreate zero-filled
images; the environment may replace them before execution and must restore
the intended external contents when resetting the model alongside the DUT.
`reset()` returns `None`. Calls read these images without modifying them and
retain no accumulator between commands. Pin latency is not represented by
extra calls.

### Transaction and memory protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

External-memory requests transfer when req_valid and req_ready are both high. Hold the request and its address/data stable while stalled. Each accepted read receives one in-order resp_valid response at least one cycle later; there is no response-ready signal. A write commits on request acceptance and has no read response. Memory contents are supplied by the environment and remain stable except for accepted writes. Memory state is not cleared by a DUT reset pin.

## Modules

`complex_conjugate_dot_product` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The external read-only memories x_mem and y_mem each contain 16 words of 128 bits. Bits [63:0] hold a signed 64-bit real component and bits [127:64] its signed imaginary component.

Let n=min(in_n,4). Interpret incx and incy as signed four-bit strides. Start at x_base+(1-n)*incx when incx<0, otherwise x_base; similarly for y. For k=0..n-1 read the pair at start+k*stride, reducing each address modulo 16. Return sum(conj(x[k])*y[k]): real=sum(xr*yr+xi*yi), imag=sum(xr*yi-xi*yr), each modulo 2^128. For n=0 return two zeros without reading memory. Stride zero repeatedly uses one cell; negative strides traverse in reverse.

This is a bounded, externally populated integer ZDOTC kernel, not a fixed-zero internal table or floating-point BLAS. Commands each produce one output and have no persistent accumulator across commands.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero. Memory request valid is cleared; the environment discards pre-reset responses.
