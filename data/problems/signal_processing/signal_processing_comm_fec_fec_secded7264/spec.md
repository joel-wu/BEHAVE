# signal_processing_comm_fec_fec_secded7264

This block implements a single-error-correcting, double-error-detecting SEC-DED (72,64) code on one 64-bit payload per transaction. In encode mode it computes the 8 parity bits defined by a fixed 8×64 binary parity matrix and outputs a 72-bit systematic codeword consisting of the parity byte followed by the original 64 data bits. In decode mode it computes the 8-bit syndrome of an input 72-bit codeword, classifies the result as no error, correctable single-bit error, or detected multi-bit error, corrects exactly one bit when the syndrome matches a weight-1 error pattern from a fixed 72-entry lookup table, and outputs the corrected 64-bit payload together with the corrected 72-bit codeword and a 2-bit status.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_secded7264 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [63:0] in_data,
    input  logic [71:0] in_codeword,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [71:0] out_codeword,
    output logic [63:0] out_data,
    output logic [1:0]  out_status
);
```

`in_mode == 1'b0` selects encode mode. `in_mode == 1'b1` selects decode mode.

### Behavior interface

```python
class SignalProcessingCommFecFecSecded7264:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_mode, in_data, in_codeword):
        pass
```

`process()` returns one output transaction per accepted input transaction with keys `out_codeword`, `out_data`, and `out_status`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_codeword`: 72.
- `out_data`: 64.
- `out_status`: 2.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_fec_fec_secded7264`: top-level SEC-DED (72,64) encoder/decoder.

## Functional Behavior

All transactions are 1:1. Each accepted input produces exactly one output transaction.

### Constants

The parity-matrix bytes are the following 64-entry table, indexed 0 through 63:

`P = [0xFF, 0x0F, 0x0F, 0x0C, 0x68, 0x88, 0x88, 0x80,
0xF0, 0xFF, 0x00, 0xF3, 0x64, 0x44, 0x44, 0x40,
0x30, 0xF0, 0xFF, 0x0F, 0x02, 0x22, 0x22, 0x26,
0xCF, 0x00, 0xF0, 0xFF, 0x01, 0x11, 0x11, 0x16,
0x68, 0x88, 0x88, 0x80, 0xFF, 0x0F, 0x00, 0xF3,
0x64, 0x44, 0x44, 0x40, 0xF0, 0xFF, 0x0F, 0x0C,
0x02, 0x22, 0x22, 0x26, 0xCF, 0x00, 0xFF, 0x0F,
0x01, 0x11, 0x11, 0x16, 0x30, 0xF0, 0xF0, 0xFF]`

The single-bit-error syndrome table is the following 72-entry table, indexed 0 through 71:

`SYNDROME_W1 = [0x0B, 0x3B, 0x37, 0x07, 0x19, 0x29, 0x49, 0x89,
0x16, 0x26, 0x46, 0x86, 0x13, 0x23, 0x43, 0x83,
0x1C, 0x2C, 0x4C, 0x8C, 0x15, 0x25, 0x45, 0x85,
0x1A, 0x2A, 0x4A, 0x8A, 0x0D, 0xCD, 0xCE, 0x0E,
0x70, 0x73, 0xB3, 0xB0, 0x51, 0x52, 0x54, 0x58,
0xA1, 0xA2, 0xA4, 0xA8, 0x31, 0x32, 0x34, 0x38,
0xC1, 0xC2, 0xC4, 0xC8, 0x61, 0x62, 0x64, 0x68,
0x91, 0x92, 0x94, 0x98, 0xE0, 0xEC, 0xDC, 0xD0,
0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]`

`popcount8(x)` means the number of ones in the 8-bit value `x`, yielding an integer in `[0,8]`.

### Byte layout

For encode mode, split `in_data[63:0]` into bytes

- `d0 = in_data[63:56]`
- `d1 = in_data[55:48]`
- `d2 = in_data[47:40]`
- `d3 = in_data[39:32]`
- `d4 = in_data[31:24]`
- `d5 = in_data[23:16]`
- `d6 = in_data[15:8]`
- `d7 = in_data[7:0]`

For decode mode, split `in_codeword[71:0]` into bytes

- `r0 = in_codeword[71:64]`
- `r1 = in_codeword[63:56]`
- `r2 = in_codeword[55:48]`
- `r3 = in_codeword[47:40]`
- `r4 = in_codeword[39:32]`
- `r5 = in_codeword[31:24]`
- `r6 = in_codeword[23:16]`
- `r7 = in_codeword[15:8]`
- `r8 = in_codeword[7:0]`

`r0` is the received parity byte. `r1` through `r8` are the received data bytes.

### Encode mode (`in_mode == 0`)

Compute an 8-bit parity byte `p` by iterating `i = 0,1,...,7` in increasing order. On each iteration:

1. Compute
   `t = popcount8(P[8*i+0] & d0) +
        popcount8(P[8*i+1] & d1) +
        popcount8(P[8*i+2] & d2) +
        popcount8(P[8*i+3] & d3) +
        popcount8(P[8*i+4] & d4) +
        popcount8(P[8*i+5] & d5) +
        popcount8(P[8*i+6] & d6) +
        popcount8(P[8*i+7] & d7)`

2. Shift the current parity byte left by one bit and insert `(t & 1)` into bit 0:
   `p = ((p << 1) | (t & 1)) & 0xFF`

After the eight iterations, the output values are:

- `out_codeword = {p, d0, d1, d2, d3, d4, d5, d6, d7}`
- `out_data = in_data`
- `out_status = 2'b00`

`in_codeword` is ignored in encode mode.

Worked example for the all-zero payload: if `in_mode = 0` and `in_data = 64'h0000000000000000`, then every `t` is zero, `p = 8'h00`, `out_codeword = 72'h000000000000000000`, `out_data = 64'h0000000000000000`, and `out_status = 2'b00`.

### Decode mode (`in_mode == 1`)

Compute an 8-bit syndrome `s` by iterating `i = 0,1,...,7` in increasing order. On each iteration:

1. Let `b = 1` if bit `(7-i)` of `r0` is set, else `b = 0`.
2. Compute
   `t = b +
        popcount8(P[8*i+0] & r1) +
        popcount8(P[8*i+1] & r2) +
        popcount8(P[8*i+2] & r3) +
        popcount8(P[8*i+3] & r4) +
        popcount8(P[8*i+4] & r5) +
        popcount8(P[8*i+5] & r6) +
        popcount8(P[8*i+6] & r7) +
        popcount8(P[8*i+7] & r8)`

3. Shift the current syndrome byte left by one bit and insert `(t & 1)` into bit 0:
   `s = ((s << 1) | (t & 1)) & 0xFF`

Let `ws = popcount8(s)`.

#### No-error case

If `ws == 0`, no correction is applied:

- corrected parity byte `p_corr = r0`
- corrected data bytes `c0 = r1`, `c1 = r2`, `c2 = r3`, `c3 = r4`, `c4 = r5`, `c5 = r6`, `c6 = r7`, `c7 = r8`
- `out_status = 2'b00`

#### Single-error case

If `ws != 0`, search `SYNDROME_W1` from index 0 upward for the first index `n` such that `SYNDROME_W1[n] == s`.

If such an index exists, this is a correctable single-bit error. Form a 9-byte error vector `e[0]..e[8]`, initially all zero. Compute

- `quot = n >> 3` and `rem = n & 7`
- `byte_index = 8 - quot`

Then set `e[byte_index] = 1 << rem` and leave all other `e[k] = 0`.

The corrected bytes are

- `p_corr = r0 ^ e[0]`
- `c0 = r1 ^ e[1]`
- `c1 = r2 ^ e[2]`
- `c2 = r3 ^ e[3]`
- `c3 = r4 ^ e[4]`
- `c4 = r5 ^ e[5]`
- `c5 = r6 ^ e[6]`
- `c6 = r7 ^ e[7]`
- `c7 = r8 ^ e[8]`

and `out_status = 2'b01`.

The `byte_index` mapping is exact. `n = 0..7` flips a bit in `e[8]`, `n = 8..15` flips a bit in `e[7]`, ..., `n = 64..71` flips a bit in `e[0]`. Thus table entries 0 through 63 correspond to the 64 data bits, and entries 64 through 71 correspond to the 8 parity bits.

Worked example for a single parity-bit error: if `in_codeword = 72'h800000000000000000`, then `s = 8'h80`, the matching table entry is `n = 71`, so `quot = 8`, `rem = 7`, `byte_index = 0`, `e[0] = 8'h80`, `p_corr = 8'h00`, `out_codeword = 72'h000000000000000000`, `out_data = 64'h0000000000000000`, and `out_status = 2'b01`.

#### Multiple-error-detected case

If `ws != 0` and no table entry matches `s`, no correction is applied:

- `p_corr = r0`
- `c0 = r1`, `c1 = r2`, `c2 = r3`, `c3 = r4`, `c4 = r5`, `c5 = r6`, `c6 = r7`, `c7 = r8`
- `out_status = 2'b10`

### Decode outputs

For both the single-error and no-correction decode cases, outputs are assembled as:

- `out_data = {c0, c1, c2, c3, c4, c5, c6, c7}`
- `out_codeword = {p_corr, c0, c1, c2, c3, c4, c5, c6, c7}`

`in_data` is ignored in decode mode.

Relative to a valid codeword, every single-bit error is corrected and every two-bit error is detected with status `2'b10`. With more than two flipped bits, use the syndrome rules above; correction or detection is not guaranteed. Status `2'b00` means zero syndrome, not proof that the received word was unchanged.

## Reset Behavior

The module uses an asynchronous active-high reset `rst`. Reset clears the pending output and all output registers to zero. Immediately after reset is asserted, and until the first accepted input transaction is captured, the output registers hold:

- `out_codeword = 72'h000000000000000000`
- `out_data = 64'h0000000000000000`
- `out_status = 2'b00`

The block has no other architectural state.
