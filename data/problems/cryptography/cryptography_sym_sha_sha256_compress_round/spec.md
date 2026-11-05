# SHA-256 Single-Block Compression

This module performs one SHA-256 compression on a single 512-bit message block and an incoming 256-bit chaining state. It implements the FIPS 180-4 SHA-256 message schedule and 64-round compression function exactly: the 16 input words are interpreted in big-endian byte order, expanded to 64 schedule words with the SHA-256 small-sigma recurrences, processed through 64 rounds using the standard `Ch`, `Maj`, `Σ0`, and `Σ1` functions and the fixed `K[0..63]` constants, and the final working variables are added modulo 2^32 back into the incoming state to produce the next chaining state.

## Interface

### RTL interface

```systemverilog
module sha256_block_compress (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_h5,
    input  logic [31:0]  in_h6,
    input  logic [31:0]  in_h7,
    input  logic [7:0]   in_b0,
    input  logic [7:0]   in_b1,
    input  logic [7:0]   in_b2,
    input  logic [7:0]   in_b3,
    input  logic [7:0]   in_b4,
    input  logic [7:0]   in_b5,
    input  logic [7:0]   in_b6,
    input  logic [7:0]   in_b7,
    input  logic [7:0]   in_b8,
    input  logic [7:0]   in_b9,
    input  logic [7:0]   in_b10,
    input  logic [7:0]   in_b11,
    input  logic [7:0]   in_b12,
    input  logic [7:0]   in_b13,
    input  logic [7:0]   in_b14,
    input  logic [7:0]   in_b15,
    input  logic [7:0]   in_b16,
    input  logic [7:0]   in_b17,
    input  logic [7:0]   in_b18,
    input  logic [7:0]   in_b19,
    input  logic [7:0]   in_b20,
    input  logic [7:0]   in_b21,
    input  logic [7:0]   in_b22,
    input  logic [7:0]   in_b23,
    input  logic [7:0]   in_b24,
    input  logic [7:0]   in_b25,
    input  logic [7:0]   in_b26,
    input  logic [7:0]   in_b27,
    input  logic [7:0]   in_b28,
    input  logic [7:0]   in_b29,
    input  logic [7:0]   in_b30,
    input  logic [7:0]   in_b31,
    input  logic [7:0]   in_b32,
    input  logic [7:0]   in_b33,
    input  logic [7:0]   in_b34,
    input  logic [7:0]   in_b35,
    input  logic [7:0]   in_b36,
    input  logic [7:0]   in_b37,
    input  logic [7:0]   in_b38,
    input  logic [7:0]   in_b39,
    input  logic [7:0]   in_b40,
    input  logic [7:0]   in_b41,
    input  logic [7:0]   in_b42,
    input  logic [7:0]   in_b43,
    input  logic [7:0]   in_b44,
    input  logic [7:0]   in_b45,
    input  logic [7:0]   in_b46,
    input  logic [7:0]   in_b47,
    input  logic [7:0]   in_b48,
    input  logic [7:0]   in_b49,
    input  logic [7:0]   in_b50,
    input  logic [7:0]   in_b51,
    input  logic [7:0]   in_b52,
    input  logic [7:0]   in_b53,
    input  logic [7:0]   in_b54,
    input  logic [7:0]   in_b55,
    input  logic [7:0]   in_b56,
    input  logic [7:0]   in_b57,
    input  logic [7:0]   in_b58,
    input  logic [7:0]   in_b59,
    input  logic [7:0]   in_b60,
    input  logic [7:0]   in_b61,
    input  logic [7:0]   in_b62,
    input  logic [7:0]   in_b63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [31:0]  out_h4,
    output logic [31:0]  out_h5,
    output logic [31:0]  out_h6,
    output logic [31:0]  out_h7
);
```

### Behavior interface

```python
class Sha256BlockCompress:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_h0, in_h1, in_h2, in_h3, in_h4, in_h5, in_h6, in_h7,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
        in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
        in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
        in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
        in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
        in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
        in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63
    ):
        pass
```

The returned dictionary contains the keys `out_h0` through `out_h7`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_h0`, `out_h1`, `out_h2`, `out_h3`, `out_h4`, `out_h5`, `out_h6`, `out_h7`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `sha256_block_compress`: top-level SHA-256 single-block compression engine.

## Functional Behavior

All state words, schedule words, working variables, constants, and outputs are 32-bit unsigned integers. Every addition is modulo 2^32. Every bitwise negation is over 32 bits. Every right shift used by SHA-256 is logical. Every rotate-right is over 32 bits.

Define `rotr32(x, n)` as:
- `((x >> n) | (x << (32 - n))) mod 2^32`
- with `n` only taking the fixed values 2, 6, 7, 10, 11, 13, 17, 18, 19, 22, and 25.

Define the SHA-256 boolean functions on 32-bit words:
- `Ch(x, y, z) = (x & y) ^ ((~x mod 2^32) & z)`
- `Maj(x, y, z) = (x & y) ^ (x & z) ^ (y & z)`

Define the SHA-256 large-sigma functions:
- `Σ0(x) = rotr32(x, 2) ^ rotr32(x, 13) ^ rotr32(x, 22)`
- `Σ1(x) = rotr32(x, 6) ^ rotr32(x, 11) ^ rotr32(x, 25)`

Define the SHA-256 small-sigma functions:
- `σ0(x) = rotr32(x, 7) ^ rotr32(x, 18) ^ (x >> 3)`
- `σ1(x) = rotr32(x, 17) ^ rotr32(x, 19) ^ (x >> 10)`

The 64 round constants are, in order:

- `K[0]  = 0x428a2f98`
- `K[1]  = 0x71374491`
- `K[2]  = 0xb5c0fbcf`
- `K[3]  = 0xe9b5dba5`
- `K[4]  = 0x3956c25b`
- `K[5]  = 0x59f111f1`
- `K[6]  = 0x923f82a4`
- `K[7]  = 0xab1c5ed5`
- `K[8]  = 0xd807aa98`
- `K[9]  = 0x12835b01`
- `K[10] = 0x243185be`
- `K[11] = 0x550c7dc3`
- `K[12] = 0x72be5d74`
- `K[13] = 0x80deb1fe`
- `K[14] = 0x9bdc06a7`
- `K[15] = 0xc19bf174`
- `K[16] = 0xe49b69c1`
- `K[17] = 0xefbe4786`
- `K[18] = 0x0fc19dc6`
- `K[19] = 0x240ca1cc`
- `K[20] = 0x2de92c6f`
- `K[21] = 0x4a7484aa`
- `K[22] = 0x5cb0a9dc`
- `K[23] = 0x76f988da`
- `K[24] = 0x983e5152`
- `K[25] = 0xa831c66d`
- `K[26] = 0xb00327c8`
- `K[27] = 0xbf597fc7`
- `K[28] = 0xc6e00bf3`
- `K[29] = 0xd5a79147`
- `K[30] = 0x06ca6351`
- `K[31] = 0x14292967`
- `K[32] = 0x27b70a85`
- `K[33] = 0x2e1b2138`
- `K[34] = 0x4d2c6dfc`
- `K[35] = 0x53380d13`
- `K[36] = 0x650a7354`
- `K[37] = 0x766a0abb`
- `K[38] = 0x81c2c92e`
- `K[39] = 0x92722c85`
- `K[40] = 0xa2bfe8a1`
- `K[41] = 0xa81a664b`
- `K[42] = 0xc24b8b70`
- `K[43] = 0xc76c51a3`
- `K[44] = 0xd192e819`
- `K[45] = 0xd6990624`
- `K[46] = 0xf40e3585`
- `K[47] = 0x106aa070`
- `K[48] = 0x19a4c116`
- `K[49] = 0x1e376c08`
- `K[50] = 0x2748774c`
- `K[51] = 0x34b0bcb5`
- `K[52] = 0x391c0cb3`
- `K[53] = 0x4ed8aa4a`
- `K[54] = 0x5b9cca4f`
- `K[55] = 0x682e6ff3`
- `K[56] = 0x748f82ee`
- `K[57] = 0x78a5636f`
- `K[58] = 0x84c87814`
- `K[59] = 0x8cc70208`
- `K[60] = 0x90befffa`
- `K[61] = 0xa4506ceb`
- `K[62] = 0xbef9a3f7`
- `K[63] = 0xc67178f2`

The 64 input bytes `in_b0` through `in_b63` form the 512-bit message block. They are assembled into the first 16 schedule words in big-endian order:
- `W[0]  = (in_b0  << 24) | (in_b1  << 16) | (in_b2  << 8) | in_b3`
- `W[1]  = (in_b4  << 24) | (in_b5  << 16) | (in_b6  << 8) | in_b7`
- ...
- `W[15] = (in_b60 << 24) | (in_b61 << 16) | (in_b62 << 8) | in_b63`

For `i = 16..63`, the remaining message schedule words are computed in ascending order by:
- `W[i] = (σ1(W[i-2]) + W[i-7] + σ0(W[i-15]) + W[i-16]) mod 2^32`

The eight working variables are initialized from the input chaining state:
- `a = in_h0`
- `b = in_h1`
- `c = in_h2`
- `d = in_h3`
- `e = in_h4`
- `f = in_h5`
- `g = in_h6`
- `h = in_h7`

The compression executes 64 rounds in order `t = 0..63`. Each round uses the standard SHA-256 update equations:
- `T1 = (h + Σ1(e) + Ch(e, f, g) + K[t] + W[t]) mod 2^32`
- `T2 = (Σ0(a) + Maj(a, b, c)) mod 2^32`
- next working variables:
  - `a' = (T1 + T2) mod 2^32`
  - `b' = a`
  - `c' = b`
  - `d' = c`
  - `e' = (d + T1) mod 2^32`
  - `f' = e`
  - `g' = f`
  - `h' = g`

After each round, `(a, b, c, d, e, f, g, h)` becomes `(a', b', c', d', e', f', g', h')`.

After round 63, the output chaining state is the input chaining state plus the final working variables, word by word modulo 2^32:
- `out_h0 = (in_h0 + a) mod 2^32`
- `out_h1 = (in_h1 + b) mod 2^32`
- `out_h2 = (in_h2 + c) mod 2^32`
- `out_h3 = (in_h3 + d) mod 2^32`
- `out_h4 = (in_h4 + e) mod 2^32`
- `out_h5 = (in_h5 + f) mod 2^32`
- `out_h6 = (in_h6 + g) mod 2^32`
- `out_h7 = (in_h7 + h) mod 2^32`

There are no special cases: every 32-bit input pattern on `in_h0..in_h7` and every 8-bit input pattern on `in_b0..in_b63` is legal and is processed exactly by the equations above.

Each accepted input transaction produces exactly one output transaction containing `out_h0..out_h7`.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

After reset, the module holds no buffered input, no pending output, and no partial schedule or round state. `in_ready` shall be high whenever the implementation can accept a new transaction and no internal state from before reset may be retained.

While no output transaction is pending, `out_valid` shall be low. The values on `out_h0` through `out_h7` are don't-care when `out_valid` is low.
