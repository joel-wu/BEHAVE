# GHASH single-block update

Return out_y=(in_y XOR in_block)*in_h in GF(2^128) with polynomial z^128+z^7+z^2+z+1.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_aead_ghash_gf128_mul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_h,
    input  logic [127:0] in_y,
    input  logic [127:0] in_block,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);
```

### Behavior interface

```python
class CryptographySymAeadGhashGf128Mul:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_h, in_y, in_block):
        pass
```

The return value of `process()` is one dictionary with key `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 128.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_sym_aead_ghash_gf128_mul` implements the public interface and operation specified here.

## Functional Behavior

Return out_y=(in_y XOR in_block)*in_h in GF(2^128) with polynomial z^128+z^7+z^2+z+1. Ports carry 16-byte big-endian strings. In GHASH bit ordering, port bit127 is the coefficient of z^0, bit126 of z^1, and bit0 of z^127. Thus the multiplicative identity is 0x80000000000000000000000000000000. A right shift with conditional XOR of 0xe1000000000000000000000000000000 implements multiplication by z, not z^(-1).

All 128-bit patterns are valid. The prior accumulator is the explicit input in_y, not hidden persistent state. No AES, automatic padding, length-block insertion or tag encryption is performed. Any equivalent field multiplier is permitted; a table or bit-serial circuit is not mandated.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
