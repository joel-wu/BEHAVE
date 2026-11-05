# Four-by-four integer matrix norms

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module matrix_norm_dlange (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_norm,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_value,

    output logic        a_req_valid,
    output logic [3:0]  a_req_addr,
    input  logic        a_req_ready,
    input  logic        a_resp_valid,
    input  logic [31:0] a_resp_rdata
);
```

### Behavior interface

```python
class MatrixNormDlange:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_norm): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_value`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory view is self.a: 16 unsigned 32-bit words. Standalone construction
starts at zero; the environment supplies the run image and preserves external memory
across reset. Memory-request pins are not return fields.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

External-memory requests transfer when req_valid and req_ready are both high. Hold the request and its address/data stable while stalled. Each accepted read receives one in-order resp_valid response at least one cycle later; there is no response-ready signal. A write commits on request acceptance and has no read response. Memory contents are supplied by the environment and remain stable except for accepted writes. Memory state is not cleared by a DUT reset pin.

## Modules

`matrix_norm_dlange` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The external read-only memory a contains 16 signed 32-bit words, with a[4*j+i]=A[i,j] (column-major). Return one unsigned 64-bit value selected by in_norm:

- 0: max over i,j of abs(A[i,j]);
- 1: max over columns j of sum_i abs(A[i,j]);
- 2: max over rows i of sum_j abs(A[i,j]);
- 3: floor(sqrt(sum over i,j of A[i,j]^2)).

All sums are exact mathematical integers; abs(-2^31)=2^31. The sum of squares can equal 2^66 and therefore needs 67 unsigned bits; its integer square root can equal 2^33 and needs 34 bits. The square root is characterized by r²<=sum<(r+1)², not by a required algorithm. This fixed-size integer specialization retains the four DLANGE norm operations without claiming floating-point rounding behavior.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero. Memory request valid is cleared; the environment discards pre-reset responses.
