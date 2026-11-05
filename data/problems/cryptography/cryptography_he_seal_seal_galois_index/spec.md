# SEAL Galois element from rotation step

This computes the get_elt_from_step mapping, not the subsequent polynomial permutation.

## Interface

### RTL interface

```systemverilog
module cryptography_he_seal_seal_galois_index (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [4:0]  in_coeff_count_power,
    input  logic [31:0] in_step,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_galois_elt,
    output logic        out_valid_step
);
```

### Behavior interface

```python
class CryptographyHeSealSealGaloisIndex:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_coeff_count_power, in_step): ...
```

Each call represents one accepted request. `in_coeff_count_power` is an
unsigned 5-bit integer and `in_step` is an unsigned 32-bit integer encoding
the signed rotation step. Return one dictionary with exactly these keys:

```python
{"out_galois_elt": ..., "out_valid_step": ...}
```

The element is an unsigned 32-bit integer and the validity flag is integer
`0` or `1`. An invalid step returns both fields as zero, not `None` or a
list. Zero step is a valid conjugation request when the power is valid.
There is no persistent algorithmic state. `reset()` returns `None` and
produces no response.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_seal_seal_galois_index` implements the public interface and operation specified here.

## Functional Behavior

This computes the get_elt_from_step mapping, not the subsequent polynomial permutation. Interpret p=in_coeff_count_power as unsigned and step=in_step as signed 32-bit. Set N=2^p. If p=0 or abs(step)>=N/2, return out_valid_step=0 and out_galois_elt=0. The magnitude of -2^31 is 2^31, not a wrapped negative value.

Otherwise return out_valid_step=1. For step=0, out_galois_elt=2N-1 (the conjugation/row-swap element, not the identity). For positive step use e=step; for negative step use e=N/2+step. Return 3^e modulo 2N. The task supports p=1..31; this extends the source's library-supported sizes while retaining the mapping. Implementations may use any exact exponentiation algorithm.

Examples: p=3 with steps 0,1,-1 gives elements 15,3,11; p=4 with step -3 gives 19.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
