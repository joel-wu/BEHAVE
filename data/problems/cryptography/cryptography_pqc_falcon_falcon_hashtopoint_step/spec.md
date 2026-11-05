# Falcon hash-to-point candidate filter

One candidate step of Falcon's variable-time hash-to-point sampler with q=12289.

## Interface

### RTL interface

```systemverilog
module FalconHashToPointStep (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_w,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [13:0] out_x
);
```

### Behavior interface

```python
class FalconHashToPointStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_w): ...
```

The `process()` return dictionary, when present, has key:
- `out_x`

Return None for in_w >= 61445; otherwise return one dictionary. Never return a list.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x`: 14.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is zero records for rejected candidates and one for accepted candidates. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`FalconHashToPointStep` implements the public interface and operation specified here.

## Functional Behavior

One candidate step of Falcon's variable-time hash-to-point sampler with q=12289. in_w is an unsigned 16-bit candidate, corresponding to source bytes (b0<<8)|b1. If in_w>=61445, the input is still consumed but emits no output. Otherwise emit one record with out_x=in_w modulo 12289, in [0,12288].

This is a 1:0-or-1 filter, not an error on rejection and not a fixed 1:1 stream. Rejected candidates must not produce dummy zero coefficients. For example 61444 emits12288, while61445 emits nothing. Random-byte generation and filling a whole polynomial remain outside this task. No constant-time or cryptographic side-channel guarantee is imposed.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
