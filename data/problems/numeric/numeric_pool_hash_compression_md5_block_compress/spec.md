# MD5 Compression Round Pipeline

This module performs one full 64-step MD5 compression transform on a single 512-bit message block and a 128-bit chaining state. Each accepted input transaction provides the sixteen 32-bit message words and the initial state words `a0`, `b0`, `c0`, and `d0`. The module applies the MD5 round functions, step constants, message-word schedule, and 32-bit left rotations exactly as defined by the 64-step recurrence implemented in the reference pipeline. The result is the post-step state `(a64, b64, c64, d64)` after step 64; no feed-forward addition with the initial state is performed.

## Interface

### RTL interface

```systemverilog
module md5_compression_round_pipeline (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_wb,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_c0,
    input  logic [31:0]  in_d0,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a64,
    output logic [31:0]  out_b64,
    output logic [31:0]  out_c64,
    output logic [31:0]  out_d64
);
```

### Behavior interface

```python
class Md5CompressionRoundPipeline:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_wb, in_a0, in_b0, in_c0, in_d0):
        pass
```

The `process(...)` method returns one dictionary with keys `out_a64`, `out_b64`, `out_c64`, and `out_d64`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a64`, `out_b64`, `out_c64`, `out_d64`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `md5_compression_round_pipeline`: top-level module implementing one accepted-input to one output-state transaction.

## Functional Behavior

The input/output cardinality is strictly 1:1. Every accepted input transaction produces exactly one output transaction.

All arithmetic is unsigned 32-bit modular arithmetic. Every addition is performed modulo `2^32`. Every bitwise complement is taken over 32 bits. Every left rotation is a 32-bit rotate-left:
`ROL32(x, s) = ((x << s) | (x >> (32 - s))) mod 2^32`.

For one accepted input transaction, split `in_wb` into sixteen 32-bit message words using little-index slicing:
- `W[0]  = in_wb[31:0]`
- `W[1]  = in_wb[63:32]`
- `W[2]  = in_wb[95:64]`
- `W[3]  = in_wb[127:96]`
- `W[4]  = in_wb[159:128]`
- `W[5]  = in_wb[191:160]`
- `W[6]  = in_wb[223:192]`
- `W[7]  = in_wb[255:224]`
- `W[8]  = in_wb[287:256]`
- `W[9]  = in_wb[319:288]`
- `W[10] = in_wb[351:320]`
- `W[11] = in_wb[383:352]`
- `W[12] = in_wb[415:384]`
- `W[13] = in_wb[447:416]`
- `W[14] = in_wb[479:448]`
- `W[15] = in_wb[511:480]`

Initialize four 32-bit working words:
- `A = in_a0`
- `B = in_b0`
- `C = in_c0`
- `D = in_d0`

Then execute 64 sequential MD5 steps. For each step `i`, compute:
- a nonlinear function `F_i`
- a message-word index `G_i`
- a 32-bit additive constant `K_i`
- a rotation amount `S_i`

Then update:
- `T = (A + F_i + K_i + W[G_i]) mod 2^32`
- `R = ROL32(T, S_i)`
- let `(A_old, B_old, C_old, D_old)` be the values of `(A, B, C, D)` before the step
- the next-state assignment is simultaneous and uses only the old values:
  - `A = D_old`
  - `B = (B_old + R) mod 2^32`
  - `C = B_old`
  - `D = C_old`

The simultaneous-update rule above is mandatory. In particular, `D` after a step is the pre-step value of `C`, not the just-updated value of `C`.

The 64 steps are exactly:

| i  | F_i                    | G_i | K_i        | S_i |
|----|------------------------|-----|------------|-----|
| 0  | `(B & C) | ((~B) & D)` | 0   | 0xD76AA478 | 7   |
| 1  | `(B & C) | ((~B) & D)` | 1   | 0xE8C7B756 | 12  |
| 2  | `(B & C) | ((~B) & D)` | 2   | 0x242070DB | 17  |
| 3  | `(B & C) | ((~B) & D)` | 3   | 0xC1BDCEEE | 22  |
| 4  | `(B & C) | ((~B) & D)` | 4   | 0xF57C0FAF | 7   |
| 5  | `(B & C) | ((~B) & D)` | 5   | 0x4787C62A | 12  |
| 6  | `(B & C) | ((~B) & D)` | 6   | 0xA8304613 | 17  |
| 7  | `(B & C) | ((~B) & D)` | 7   | 0xFD469501 | 22  |
| 8  | `(B & C) | ((~B) & D)` | 8   | 0x698098D8 | 7   |
| 9  | `(B & C) | ((~B) & D)` | 9   | 0x8B44F7AF | 12  |
| 10 | `(B & C) | ((~B) & D)` | 10  | 0xFFFF5BB1 | 17  |
| 11 | `(B & C) | ((~B) & D)` | 11  | 0x895CD7BE | 22  |
| 12 | `(B & C) | ((~B) & D)` | 12  | 0x6B901122 | 7   |
| 13 | `(B & C) | ((~B) & D)` | 13  | 0xFD987193 | 12  |
| 14 | `(B & C) | ((~B) & D)` | 14  | 0xA679438E | 17  |
| 15 | `(B & C) | ((~B) & D)` | 15  | 0x49B40821 | 22  |
| 16 | `(D & B) | ((~D) & C)` | 1   | 0xF61E2562 | 5   |
| 17 | `(D & B) | ((~D) & C)` | 6   | 0xC040B340 | 9   |
| 18 | `(D & B) | ((~D) & C)` | 11  | 0x265E5A51 | 14  |
| 19 | `(D & B) | ((~D) & C)` | 0   | 0xE9B6C7AA | 20  |
| 20 | `(D & B) | ((~D) & C)` | 5   | 0xD62F105D | 5   |
| 21 | `(D & B) | ((~D) & C)` | 10  | 0x02441453 | 9   |
| 22 | `(D & B) | ((~D) & C)` | 15  | 0xD8A1E681 | 14  |
| 23 | `(D & B) | ((~D) & C)` | 4   | 0xE7D3FBC8 | 20  |
| 24 | `(D & B) | ((~D) & C)` | 9   | 0x21E1CDE6 | 5   |
| 25 | `(D & B) | ((~D) & C)` | 14  | 0xC33707D6 | 9   |
| 26 | `(D & B) | ((~D) & C)` | 3   | 0xF4D50D87 | 14  |
| 27 | `(D & B) | ((~D) & C)` | 8   | 0x455A14ED | 20  |
| 28 | `(D & B) | ((~D) & C)` | 13  | 0xA9E3E905 | 5   |
| 29 | `(D & B) | ((~D) & C)` | 2   | 0xFCEFA3F8 | 9   |
| 30 | `(D & B) | ((~D) & C)` | 7   | 0x676F02D9 | 14  |
| 31 | `(D & B) | ((~D) & C)` | 12  | 0x8D2A4C8A | 20  |
| 32 | `B ^ C ^ D`            | 5   | 0xFFFA3942 | 4   |
| 33 | `B ^ C ^ D`            | 8   | 0x8771F681 | 11  |
| 34 | `B ^ C ^ D`            | 11  | 0x6D9D6122 | 16  |
| 35 | `B ^ C ^ D`            | 14  | 0xFDE5380C | 23  |
| 36 | `B ^ C ^ D`            | 1   | 0xA4BEEA44 | 4   |
| 37 | `B ^ C ^ D`            | 4   | 0x4BDECFA9 | 11  |
| 38 | `B ^ C ^ D`            | 7   | 0xF6BB4B60 | 16  |
| 39 | `B ^ C ^ D`            | 10  | 0xBEBFBC70 | 23  |
| 40 | `B ^ C ^ D`            | 13  | 0x289B7EC6 | 4   |
| 41 | `B ^ C ^ D`            | 0   | 0xEAA127FA | 11  |
| 42 | `B ^ C ^ D`            | 3   | 0xD4EF3085 | 16  |
| 43 | `B ^ C ^ D`            | 6   | 0x04881D05 | 23  |
| 44 | `B ^ C ^ D`            | 9   | 0xD9D4D039 | 4   |
| 45 | `B ^ C ^ D`            | 12  | 0xE6DB99E5 | 11  |
| 46 | `B ^ C ^ D`            | 15  | 0x1FA27CF8 | 16  |
| 47 | `B ^ C ^ D`            | 2   | 0xC4AC5665 | 23  |
| 48 | `C ^ (B | (~D))`       | 0   | 0xF4292244 | 6   |
| 49 | `C ^ (B | (~D))`       | 7   | 0x432AFF97 | 10  |
| 50 | `C ^ (B | (~D))`       | 14  | 0xAB9423A7 | 15  |
| 51 | `C ^ (B | (~D))`       | 5   | 0xFC93A039 | 21  |
| 52 | `C ^ (B | (~D))`       | 12  | 0x655B59C3 | 6   |
| 53 | `C ^ (B | (~D))`       | 3   | 0x8F0CCC92 | 10  |
| 54 | `C ^ (B | (~D))`       | 10  | 0xFFEFF47D | 15  |
| 55 | `C ^ (B | (~D))`       | 1   | 0x85845DD1 | 21  |
| 56 | `C ^ (B | (~D))`       | 8   | 0x6FA87E4F | 6   |
| 57 | `C ^ (B | (~D))`       | 15  | 0xFE2CE6E0 | 10  |
| 58 | `C ^ (B | (~D))`       | 6   | 0xA3014314 | 15  |
| 59 | `C ^ (B | (~D))`       | 13  | 0x4E0811A1 | 21  |
| 60 | `C ^ (B | (~D))`       | 4   | 0xF7537E82 | 6   |
| 61 | `C ^ (B | (~D))`       | 11  | 0xBD3AF235 | 10  |
| 62 | `C ^ (B | (~D))`       | 2   | 0x2AD7D2BB | 15  |
| 63 | `C ^ (B | (~D))`       | 9   | 0xEB86D391 | 21  |

After step 63 completes, the output transaction is:
- `out_a64 = A`
- `out_b64 = B`
- `out_c64 = C`
- `out_d64 = D`

A worked final-step example of the state update rule is:
if the state entering step 63 is `(A63, B63, C63, D63)`, then:
- `T63 = (A63 + (C63 ^ (B63 | (~D63))) + 0xEB86D391 + W[9]) mod 2^32`
- `R63 = ROL32(T63, 21)`
- `out_a64 = D63`
- `out_b64 = (B63 + R63) mod 2^32`
- `out_c64 = B63`
- `out_d64 = C63`

All returned output values are 32-bit unsigned bit patterns.

## Reset Behavior

The module uses synchronous active-high reset `rst`.

On reset:
- `out_valid = 0`
- `out_a64 = 0x00000000`
- `out_b64 = 0x00000000`
- `out_c64 = 0x00000000`
- `out_d64 = 0x00000000`

The design has no architectural state beyond any internal pipeline or buffering required to support the handshake protocol. On reset, every internal valid bit shall be cleared to `0`, and every internal data register that can influence future outputs shall be set to `0x00000000`.
