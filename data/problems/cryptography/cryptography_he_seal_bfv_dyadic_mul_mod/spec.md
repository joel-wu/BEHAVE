# Dyadic Barrett residue multiplication

This task exposes the scalar multiplication/reduction used by SEAL, including its supplied reciprocal.

## Interface

### RTL interface

```systemverilog
module cryptography_he_seal_bfv_dyadic_mul_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_operand2,
    input  logic [63:0]  in_modulus,
    input  logic [63:0]  in_const_ratio_0,
    input  logic [63:0]  in_const_ratio_1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);
```

### Behavior interface

```python
class DyadicModularMultiply:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_operand1, in_operand2, in_modulus, in_const_ratio_0, in_const_ratio_1):
        pass
```

Each call represents one accepted request. All five arguments are unsigned
64-bit integers. Return one dictionary with exactly the key `out_result`:

```python
{"out_result": ...}
```

The value is an unsigned 64-bit integer, not a list or `None`. Every request
produces this shape, including zero modulus or an inexact reciprocal; the
word rules below determine its value. There is no persistent algorithmic
state. `reset()` returns `None` and produces no response.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_seal_bfv_dyadic_mul_mod` implements the public interface and operation specified here.

## Functional Behavior

This task exposes the scalar multiplication/reduction used by SEAL, including its supplied reciprocal. Let a,b,p,r0,r1 be the unsigned inputs and mu=r0+2^64*r1. Compute with exact integer intermediates:

```
z = a*b
Q = floor(z*mu / 2^128) modulo 2^64
t = (z-Q*p) modulo 2^64
out_result = t-p if t >= p else t
```

For 2 <= p < 2^63 and mu=floor(2^128/p), this equals (a*b) modulo p for arbitrary 64-bit a,b. The reciprocal's low word is r0 and high word r1. In particular p=1 cannot supply its exact reciprocal in 128 bits and is not in this source-equivalence domain. The word rules define all bit patterns, including bad reciprocals and p=0; no validity check is implied. Any equivalent implementation is permitted, not only the source's limb schedule.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
