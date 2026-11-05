# BN254 base-field addition

This task adds canonical integers in BN254's base field Fp, not its scalar field Fr.

## Interface

### RTL interface

```systemverilog
module cryptography_zk_bn254_bn254_fp_add (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [253:0] in_a,
    input  logic [253:0] in_b,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [253:0] out_sum
);
```

### Behavior interface

```python
class CryptographyZkBn254Bn254FpAdd:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_b): ...
```

The return value of `process()` is one dict with key `out_sum`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sum`: 254.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_zk_bn254_bn254_fp_add` implements the public interface and operation specified here.

## Functional Behavior

This task adds canonical integers in BN254's base field Fp, not its scalar field Fr. The base-field prime is:

```
p = 21888242871839275222246405745257275088696311157297823662689037894645226208583
  = 0x30644e72e131a029b85045b68181585d97816a916871ca8d3c208c16d87cfd47
```

For every pair of unsigned 254-bit inputs return out_sum=(in_a+in_b) modulo p in canonical [0,p). The task also normalizes noncanonical port patterns. Retain the full 255-bit sum before reduction; at most two subtractions of p suffice for these widths. No particular reduction circuit is required. The ports carry ordinary representatives, not Montgomery encodings; no curve-point or pairing operation is included.

## Reset Behavior

Reset is active-high and asynchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
