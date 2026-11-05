# Sixteen-word integer scale in memory

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module vector_scale_16x32_burst (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_alpha,
    input  logic [15:0] in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [4:0]  out_count,

    output logic        x_req_valid,
    output logic        x_req_we,
    output logic [15:0] x_req_addr,
    output logic [31:0] x_req_wdata,
    input  logic        x_req_ready,
    input  logic        x_resp_valid,
    input  logic [31:0] x_resp_rdata
);
```

### Behavior interface

```python
class VectorScale16x32Burst:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_alpha, in_base_addr): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_count`: 5.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory view is self.x: 65536 unsigned 32-bit words. Standalone construction
starts it at zero; the environment supplies the run image and preserves accepted
writes across reset. The returned count does not replace the required memory writes.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

External-memory requests transfer when req_valid and req_ready are both high. Hold the request and its address/data stable while stalled. Each accepted read receives one in-order resp_valid response at least one cycle later; there is no response-ready signal. A write commits on request acceptance and has no read response. Memory contents are supplied by the environment and remain stable except for accepted writes. Memory state is not cleared by a DUT reset pin.

## Modules

`vector_scale_16x32_burst` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The external read-write memory x contains 65536 unsigned 32-bit words. For a command (alpha,base), if alpha=1 return out_count=0 with no memory accesses. Otherwise, for i=0..15 replace x[(base+i) mod 65536] by alpha*x[(base+i) mod 65536] mod 2^32; return out_count=16 after all writes complete. Other cells are unchanged. alpha=0 writes zeros.

This retains DSCAL's element-wise scaling as a fixed 16-word integer kernel, not floating-point BLAS. Each command emits one completion transaction.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero. Memory request valid is cleared; the environment discards pre-reset responses.
