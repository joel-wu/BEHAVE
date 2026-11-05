# Kyber centered Barrett reduction

Interpret in_a as signed 16-bit a.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_barrett_reduce (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r
);
```

### Behavior interface

```python
class CryptographyPqcKyberKyberBarrettReduce:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a):
        pass
```

The `process()` method returns a dictionary with one key:
- `out_r`: 16-bit unsigned integer carrying the two's-complement bit pattern of the reduced signed result.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_r`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_barrett_reduce` implements the public interface and operation specified here.

## Functional Behavior

Interpret in_a as signed 16-bit a. With q=3329, return the 16-bit two's-complement encoding of a-q*floor((20159*a+2^25)/2^26). Division is rounded toward negative infinity. All arithmetic before the final encoding is exact.

For every 16-bit input, the result lies in [-1664,1664] and is congruent to a modulo q. This is a centered representative, not an unsigned remainder. For example 3329 and -3329 both map to zero; 1665 maps to -1664. Any implementation with these exact results is allowed.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
