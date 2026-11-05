# WOTS base-16 message and checksum digit

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_sphincs_sphincs_wots_chain_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [6:0]   in_chain_idx,
    input  logic [255:0] in_msg,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [3:0]   out_chain_step
);
```

### Behavior interface

```python
class cryptography_pqc_sphincs_sphincs_wots_chain_step:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_chain_idx, in_msg): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_chain_step`: 4.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_pqc_sphincs_sphincs_wots_chain_step` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Interpret the 256-bit message as 64 hexadecimal digits d[0]..d[63], most significant nibble first. Form c=sum(15-d[i]) and append the three checksum digits (c>>8)&15, (c>>4)&15 and c&15. Return digit in_chain_idx of this 67-digit sequence; indices 67..127 return zero.

This fixed n=32, w=16 kernel supplies a WOTS chain length. It does not execute a hash chain, address generation, or a complete SPHINCS+ signature.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
