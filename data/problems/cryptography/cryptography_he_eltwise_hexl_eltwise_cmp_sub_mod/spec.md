# Conditional modular subtraction

This is the scalar EltwiseCmpSubMod operation.

## Interface

### RTL interface

```systemverilog
module cryptography_he_eltwise_hexl_eltwise_cmp_sub_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_modulus,
    input  logic [2:0]   in_cmp,
    input  logic [63:0]  in_bound,
    input  logic [63:0]  in_diff,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);
```

### Behavior interface

```python
class CryptographyHeEltwiseHexlEltwiseCmpSubMod:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_operand1, in_modulus, in_cmp, in_bound, in_diff): ...
```

Call `process` once per accepted request, passing the input port patterns as
nonnegative integers. It returns exactly `{"out_result": result}`, where
`result` is a nonnegative integer fitting 64 bits. Zero modulus or a zero
result still produces this dictionary, not `None` or an empty sequence.
The model has no persistent algorithmic state; `reset()` is a no-op.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_eltwise_hexl_eltwise_cmp_sub_mod` implements the public interface and operation specified here.

## Functional Behavior

This is the scalar EltwiseCmpSubMod operation. Interpret x=in_operand1, q=in_modulus, b=in_bound and d=in_diff as unsigned 64-bit integers. in_cmp codes 0,1,2,3,4,5 mean respectively x==b, x!=b, x<b, x<=b, x>b, x>=b; codes 6 and 7 are false. These are task encodings, not a required C++ enum ABI.

If q=0, return zero. Otherwise return (x-d) modulo q when the predicate is true and x modulo q when false. Modulo here is the nonnegative mathematical remainder: a negative subtraction must not first wrap to 64 bits. All input patterns are defined. This computes one element, not a vector comparison mask.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
