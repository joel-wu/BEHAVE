# Sixteen-coefficient Galois permutation in memory

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module galois_permutation_core (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [4:0]  in_base_addr,
    input  logic [4:0]  in_galois_elt,
    input  logic [7:0]  in_modulus,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [4:0]  out_base_addr,

    output logic        coeff_mem_req_valid,
    output logic        coeff_mem_req_we,
    output logic [4:0]  coeff_mem_req_addr,
    output logic [7:0]  coeff_mem_req_wdata,
    input  logic        coeff_mem_req_ready,
    input  logic        coeff_mem_resp_valid,
    input  logic [7:0]  coeff_mem_resp_rdata
);
```

### Behavior interface

```python
class GaloisPermutationCore:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_mode, in_base_addr, in_galois_elt, in_modulus): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_base_addr`: 5.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.coeff_mem is the read-write view of 32 unsigned eight-bit words. Standalone reset
creates a zero-filled placeholder; the environment binds its configured image before
processing and across resets. Preserve the specified in-place writes; out_base_addr is
only the completion field.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

External-memory requests transfer when req_valid and req_ready are both high. Hold the request and its address/data stable while stalled. Each accepted read receives one in-order resp_valid response at least one cycle later; there is no response-ready signal. A write commits on request acceptance and has no read response. Memory contents are supplied by the environment and remain stable except for accepted writes. Memory state is not cleared by a DUT reset pin.

## Modules

`galois_permutation_core` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The external read-write memory coeff_mem contains 32 unsigned eight-bit words. Snapshot operand[i]=coeff_mem[(base_addr+i) mod 32] for i=0..15 before modifying memory. Addresses in the destination window are (base_addr+i) mod 32; the same window is updated in place.

In coefficient mode (in_mode=0), initialize result[0..15] to zero. For i=0..15 in order, let k=i*g, j=k mod 16. Assign result[j]=operand[i] when bit 4 of k is zero or operand[i]=0; otherwise assign (q-operand[i]) mod 256. If an even g maps multiple inputs to the same j, the last assignment wins and unassigned cells remain zero.

In NTT mode (in_mode=1), define rev_w as reversal of exactly w bits. Set result[i]=operand[rev_4(((g*rev_5(16+i))>>1) mod 16)]. q is ignored. Write all 16 result cells and return out_base_addr=base_addr. Other memory cells are unchanged.

For odd g and valid coefficient residues this is the degree-16 negacyclic Galois action. The formulas also define even g and raw byte inputs as task extensions; they are not all field automorphisms.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero. Memory request valid is cleared; the environment discards pre-reset responses.
