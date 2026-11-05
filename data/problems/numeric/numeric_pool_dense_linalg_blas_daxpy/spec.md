# Indexed integer AXPY update

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module daxpy_element_update (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_n,
    input  logic [7:0]   in_k,
    input  logic [63:0]  in_da,
    input  logic [7:0]   in_incx,
    input  logic [7:0]   in_incy,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_done,

    output logic         dx_req_valid,
    output logic [7:0]   dx_req_addr,
    input  logic         dx_req_ready,
    input  logic         dx_resp_valid,
    input  logic [63:0]  dx_resp_rdata,

    output logic         dy_req_valid,
    output logic         dy_req_we,
    output logic [7:0]   dy_req_addr,
    output logic [63:0]  dy_req_wdata,
    input  logic         dy_req_ready,
    input  logic         dy_resp_valid,
    input  logic [63:0]  dy_resp_rdata
);
```

### Behavior interface

```python
class DaxpyElementUpdate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_n, in_k, in_da, in_incx, in_incy): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_done`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory views are self.dx and self.dy, each 256 unsigned 64-bit words.
Standalone construction starts them at zero; the environment supplies the run images
and preserves accepted writes across reset. Memory side effects are not replaced by
out_done.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

External-memory requests transfer when req_valid and req_ready are both high. Hold the request and its address/data stable while stalled. Each accepted read receives one in-order resp_valid response at least one cycle later; there is no response-ready signal. A write commits on request acceptance and has no read response. Memory contents are supplied by the environment and remain stable except for accepted writes. Memory state is not cleared by a DUT reset pin.

## Modules

`daxpy_element_update` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The external read-only memory dx contains 256 unsigned 64-bit words; the external read-write memory dy contains 256 unsigned 64-bit words. incx and incy are signed eight-bit strides. n and k are unsigned eight-bit values and da is an unsigned 64-bit coefficient.

If n=0, k>=n, or da=0, perform no memory operation and return out_done=1. Otherwise let ix=((1-n)*incx if incx<0 else 0)+k*incx, and similarly iy. Reduce both addresses modulo 256. Read dx[ix] and dy[iy], write (dy[iy]+da*dx[ix]) mod 2^64 to dy[iy], then return out_done=1. All other dy words remain unchanged.

This is one indexed element of integer AXPY, preserving BLAS negative-stride indexing but not claiming floating-point arithmetic or a full n-element loop.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero. Memory request valid is cleared; the environment discards pre-reset responses.
