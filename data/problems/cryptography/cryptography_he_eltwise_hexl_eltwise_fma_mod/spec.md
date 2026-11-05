# HE fused multiply-add modulo

This scalar instance of HEXL EltwiseFMAMod returns (in_arg1*in_arg2 + addend) modulo in_modulus, where addend is in_arg3 if in_arg3_enable is 1 and zero otherwise.

## Interface

### RTL interface

```systemverilog
module cryptography_he_eltwise_hexl_eltwise_fma_mod (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_arg1,
    input  logic [63:0] in_arg2,
    input  logic [63:0] in_arg3,
    input  logic        in_arg3_enable,
    input  logic [63:0] in_modulus,
    input  logic [3:0]  in_input_mod_factor,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_result
);
```

### Behavior interface

```python
class CryptographyHeEltwiseHexlEltwiseFmaMod:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_arg1, in_arg2, in_arg3, in_arg3_enable, in_modulus, in_input_mod_factor):
        pass
```

Call `process` once per accepted request, passing the input port patterns as
nonnegative integers. It returns exactly `{"out_result": result}`, where
`result` is a nonnegative integer fitting 64 bits. Modulus zero or one and all
other zero results still produce this dictionary, not `None` or an empty list.
The model has no persistent algorithmic state; `reset()` is a no-op.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_eltwise_hexl_eltwise_fma_mod` implements the public interface and operation specified here.

## Functional Behavior

This scalar instance of HEXL EltwiseFMAMod returns (in_arg1*in_arg2 + addend) modulo in_modulus, where addend is in_arg3 if in_arg3_enable is 1 and zero otherwise. For modulus 0 or 1, return zero. Products and the addition are exact before reduction; do not truncate to 64 bits.

The source domain is 2 <= modulus < 2^61, input_mod_factor in {1,2,4,8}, and each used operand below input_mod_factor*modulus. This task uses the same arithmetic for all other 64-bit operands and moduli >= 2. input_mod_factor describes that source-domain bound, not an arithmetic multiplier. Disabled arg3 is ignored. The result is one unsigned 64-bit word; no specific reduction algorithm is required.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
