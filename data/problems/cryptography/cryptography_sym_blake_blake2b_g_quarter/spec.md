# BLAKE2b G Quarter

This block computes one BLAKE2b quarter-round function `G` on four 64-bit working words and two 64-bit message words. The arithmetic is the exact BLAKE2b integer mixing operation: 64-bit modulo addition, bitwise XOR, and 64-bit right rotation by fixed counts 32, 24, 16, and 63. Each accepted input transaction produces one output transaction containing the updated four working words.

## Interface

### RTL interface

```systemverilog
module cryptography_sym_blake_blake2b_g_quarter (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a,
    input  logic [63:0]  in_b,
    input  logic [63:0]  in_c,
    input  logic [63:0]  in_d,
    input  logic [63:0]  in_m0,
    input  logic [63:0]  in_m1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a,
    output logic [63:0]  out_b,
    output logic [63:0]  out_c,
    output logic [63:0]  out_d
);
```

### Behavior interface

```python
class CryptographySymBlakeBlake2BGQuarter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_b, in_c, in_d, in_m0, in_m1): ...
```

`process()` returns a dict with keys `out_a`, `out_b`, `out_c`, and `out_d`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a`, `out_b`, `out_c`, `out_d`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_sym_blake_blake2b_g_quarter`: top-level module implementing one BLAKE2b `G` quarter-round.

## Functional Behavior

All inputs and outputs are 64-bit unsigned integers. Every addition is modulo `2^64`. Every XOR is bitwise on 64-bit words. Every rotation is on a 64-bit word.

Define `ROTR64(x, n)` as a 64-bit rotate right by constant count `n`:

- `ROTR64(x, 32) = ((x >> 32) | (x << 32)) mod 2^64`
- `ROTR64(x, 24) = ((x >> 24) | (x << 40)) mod 2^64`
- `ROTR64(x, 16) = ((x >> 16) | (x << 48)) mod 2^64`
- `ROTR64(x, 63) = ((x >> 63) | (x << 1)) mod 2^64`

For one accepted input transaction, the block computes the following sequence in order, using the input values as the initial values of the working words:

1. `a1 = (in_a + in_b + in_m0) mod 2^64`
2. `d1 = ROTR64(in_d xor a1, 32)`
3. `c1 = (in_c + d1) mod 2^64`
4. `b1 = ROTR64(in_b xor c1, 24)`
5. `a2 = (a1 + b1 + in_m1) mod 2^64`
6. `d2 = ROTR64(d1 xor a2, 16)`
7. `c2 = (c1 + d2) mod 2^64`
8. `b2 = ROTR64(b1 xor c2, 63)`

The output transaction is:

- `out_a = a2`
- `out_b = b2`
- `out_c = c2`
- `out_d = d2`

There are no special cases. Every 64-bit input bit pattern is legal and is processed by the same equations above.

## Reset Behavior

Reset is synchronous active-high `rst`.

Reset discards any pending output and sets `out_valid = 0` and `out_a = out_b = out_c = out_d = 0`. Outside reset, output data is meaningful only while `out_valid = 1`; it must remain stable until accepted. There is no retained algorithmic state across transactions.
