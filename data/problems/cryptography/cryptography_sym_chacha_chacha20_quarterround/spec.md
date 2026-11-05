# ChaCha20 Quarter Round

This block computes one ChaCha20 quarter round on four 32-bit input words. The computation is the standard ChaCha quarter-round sequence: four 32-bit modulo-\(2^{32}\) additions, four 32-bit XOR operations, and four fixed left rotations by 16, 12, 8, and 7 bits. Each accepted input transaction produces exactly one output transaction containing the transformed four words.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_chacha_chacha20_quarterround (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,
    input  logic [31:0] in_b,
    input  logic [31:0] in_c,
    input  logic [31:0] in_d,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a,
    output logic [31:0] out_b,
    output logic [31:0] out_c,
    output logic [31:0] out_d
);
```

### Behavior interface

```python
class ChaCha20QuarterRound:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a, in_b, in_c, in_d):
        pass
```

The `process()` return value is one dictionary with keys `out_a`, `out_b`, `out_c`, and `out_d`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a`, `out_b`, `out_c`, `out_d`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_sym_chacha_chacha20_quarterround`: top-level module implementing one ChaCha20 quarter round.

## Functional Behavior

All inputs and outputs are 32-bit unsigned integers.

Arithmetic and bitwise rules:

- Every addition is unsigned addition modulo \(2^{32}\). Equivalently, the mathematical sum is truncated to its least-significant 32 bits.
- Every XOR is bitwise XOR on 32-bit values.
- Every rotation is a 32-bit rotate-left. For a 32-bit value `x` and rotation distance `n`, `rotl32(x, n) = ((x << n) | (x >> (32 - n))) mod 2^32`, where both shifts operate on the 32-bit bit pattern of `x`.
- Intermediate results are truncated to 32 bits after each addition and after each rotation. XOR already produces a 32-bit result.

The quarter round is performed in this exact order, using the updated value from each preceding step:

1. `a1 = (in_a + in_b) mod 2^32`
2. `d1 = in_d XOR a1`
3. `d2 = rotl32(d1, 16)`
4. `c1 = (in_c + d2) mod 2^32`
5. `b1 = in_b XOR c1`
6. `b2 = rotl32(b1, 12)`
7. `a2 = (a1 + b2) mod 2^32`
8. `d3 = d2 XOR a2`
9. `d4 = rotl32(d3, 8)`
10. `c2 = (c1 + d4) mod 2^32`
11. `b3 = b2 XOR c2`
12. `b4 = rotl32(b3, 7)`

The outputs are:

- `out_a = a2`
- `out_b = b4`
- `out_c = c2`
- `out_d = d4`

Worked example:

- If `in_a = 0x11111111`, `in_b = 0x01020304`, `in_c = 0x9b8d6f43`, and `in_d = 0x01234567`,
  then the output shall be
  `out_a = 0xea2a92f4`,
  `out_b = 0xcb1cf8ce`,
  `out_c = 0x4581472e`,
  `out_d = 0x5881c4bb`.

There are no special cases: all 32-bit input bit patterns are legal and are processed by the same rules above.

## Reset Behavior

`rst` is active-high. Reset is synchronous.

This block has no architectural state. After reset, the block is idle with no pending output transaction.

Required reset values:

- `in_ready = 1`
- `out_valid = 0`
- `out_a = 32'h00000000`
- `out_b = 32'h00000000`
- `out_c = 32'h00000000`
- `out_d = 32'h00000000`

The block retains no computational state between transactions.
