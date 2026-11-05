# HE residue addition

One scalar element of HEXL EltwiseAddMod.

## Interface

### RTL interface

```systemverilog
module cryptography_he_eltwise_hexl_eltwise_add_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_operand2,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);
```

### Behavior interface

```python
class CryptographyHeEltwiseHexlEltwiseAddMod:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_operand1, in_operand2, in_modulus): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_result": ...}`, whose value is a Python integer holding the specified
output port bit pattern.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_he_eltwise_hexl_eltwise_add_mod` implements the public interface and operation specified here.

## Functional Behavior

One scalar element of HEXL EltwiseAddMod. Let a, b and q be the three unsigned 64-bit inputs. For canonical residues a,b < q with 2 <= q < 2^63, return (a+b) modulo q. The full sum must not lose a carry within this domain.

For every other input pattern the specified extension is: s = (a+b) modulo 2^64; return s-q when q != 0 and s >= q, otherwise s. This extension is not a general modular adder for noncanonical operands. The interface is scalar; it has no hidden vector length or memory.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
