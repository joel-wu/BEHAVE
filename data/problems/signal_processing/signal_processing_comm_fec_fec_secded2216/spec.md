# SECDED(22,16) Symbol Codec

This block implements a single-symbol SECDED(22,16) forward-error-correction codec on 16-bit data words. It supports both encoding and decoding in one transaction-selected datapath. Encoding computes 6 parity bits from a fixed 6×16 binary matrix and outputs the 22-bit codeword split across three fields. Decoding computes the 6-bit syndrome from the received 22-bit word, classifies the error pattern as no error, correctable single-bit error, or detected uncorrectable error, corrects only a matched single-bit error, and returns the corrected 16-bit data word together with the error classification.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_secded2216 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [5:0]  in_parity,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [5:0]  out_parity,
    output logic [7:0]  out_data0,
    output logic [7:0]  out_data1,
    output logic [1:0]  out_status
);
```

### Behavior interface

```python
class SignalProcessingCommFecFecSecded2216:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_encode, in_parity, in_data0, in_data1): ...
```

`process()` returns a dict with keys:
- `out_parity`
- `out_data0`
- `out_data1`
- `out_status`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data0`, `out_data1`: 8.
- `out_parity`: 6.
- `out_status`: 2.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_fec_fec_secded2216`: top-level SECDED(22,16) symbol encoder/decoder.

## Functional Behavior

`in_encode` selects the operation:
- `in_encode = 1`: encode mode.
- `in_encode = 0`: decode mode.

All ports are unsigned integer bit patterns. `in_parity` and `out_parity` are 6-bit values. `in_data0`, `in_data1`, `out_data0`, and `out_data1` are 8-bit values. `out_status` is a 2-bit status code.

The 16-bit data word is formed as:
- bits `[15:8] = in_data0`
- bits `[7:0]  = in_data1`

Equivalently, byte 0 is the most-significant data byte and byte 1 is the least-significant data byte.

The fixed parity matrix `P` has 6 rows, each row encoded as a 16-bit mask with bits `[15:8]` in the first byte and bits `[7:0]` in the second byte:

- row 0: `16'h993C`
- row 1: `16'h3E8A`
- row 2: `16'hEE60`
- row 3: `16'hE1D1`
- row 4: `16'h13C7`
- row 5: `16'h443F`

The single-bit-error syndrome table has 22 entries indexed by bit location `n` from 0 to 21:

- `n=0  -> 6'h07`
- `n=1  -> 6'h13`
- `n=2  -> 6'h23`
- `n=3  -> 6'h31`
- `n=4  -> 6'h25`
- `n=5  -> 6'h29`
- `n=6  -> 6'h0E`
- `n=7  -> 6'h16`
- `n=8  -> 6'h26`
- `n=9  -> 6'h1A`
- `n=10 -> 6'h19`
- `n=11 -> 6'h38`
- `n=12 -> 6'h32`
- `n=13 -> 6'h1C`
- `n=14 -> 6'h0D`
- `n=15 -> 6'h2C`
- `n=16 -> 6'h01`
- `n=17 -> 6'h02`
- `n=18 -> 6'h04`
- `n=19 -> 6'h08`
- `n=20 -> 6'h10`
- `n=21 -> 6'h20`

Bit location `n` counts from the least-significant bit of `{parity, data0, data1}`:
- locations `0..7` correspond to `data1[n]`,
- locations `8..15` correspond to `data0[n-8]`,
- locations `16..21` correspond to `parity[n-16]`.

For syndrome matching and error-vector construction, the three-byte codeword container is ordered as:
- byte 0 = parity byte, with only bits `[5:0]` meaningful and bits `[7:6]` ignored,
- byte 1 = `data0`,
- byte 2 = `data1`.

### Encode mode (`in_encode = 1`)

The encoder computes the 6-bit parity field by processing the six rows of `P` in row order from row 0 to row 5. For row `i`, let `mask_i` be the 16-bit row mask. Compute:
- `x_i = popcount(mask_i & {in_data0, in_data1}) mod 2`

The output parity is assembled MSB-first:
- after row 0, `x_0` becomes `out_parity[5]`
- after row 1, `x_1` becomes `out_parity[4]`
- after row 2, `x_2` becomes `out_parity[3]`
- after row 3, `x_3` becomes `out_parity[2]`
- after row 4, `x_4` becomes `out_parity[1]`
- after row 5, `x_5` becomes `out_parity[0]`

Equivalently:
- `out_parity = {x_0, x_1, x_2, x_3, x_4, x_5}`

In encode mode:
- `out_parity` is the computed parity.
- `out_data0 = in_data0`
- `out_data1 = in_data1`
- `out_status = 2'b00`

### Decode mode (`in_encode = 0`)

The received symbol is:
- parity byte = `in_parity[5:0]`
- data byte 0 = `in_data0`
- data byte 1 = `in_data1`

The 6-bit syndrome is computed by processing the six rows of `P` in row order from row 0 to row 5. For row `i`:
- let `pbit_i` be parity input bit `in_parity[5-i]`
- let `mask_i` be row `i` of `P`
- let `m_i = popcount(mask_i & {in_data0, in_data1}) mod 2`
- let `s_i = pbit_i xor m_i`

The syndrome is assembled MSB-first:
- `syndrome = {s_0, s_1, s_2, s_3, s_4, s_5}`

Status classification is:
- `out_status = 2'b00` if `syndrome == 6'h00`
- `out_status = 2'b01` if `syndrome != 6'h00` and `syndrome` matches one entry in the 22-entry single-bit-error syndrome table
- `out_status = 2'b10` if `syndrome != 6'h00` and `syndrome` matches no entry in the table

When `out_status = 2'b00`, no correction is applied:
- `out_data0 = in_data0`
- `out_data1 = in_data1`
- `out_parity = in_parity`

When `out_status = 2'b01`, a 24-bit error vector container `{e_hat[0], e_hat[1], e_hat[2]}` is initialized to zero and exactly one bit is set from the matched table index `n`:
- `quot = floor(n / 8)`
- `rem  = n - 8*quot`
- set bit `rem` of byte index `2 - quot`

This gives:
- if `0 <= n <= 7`, the error bit is in `data1`,
- if `8 <= n <= 15`, the error bit is in `data0`,
- if `16 <= n <= 21`, the error bit is in the parity byte.

The corrected outputs are:
- `out_parity = in_parity xor e_hat[0][5:0]`
- `out_data0 = in_data0 xor e_hat[1]`
- `out_data1 = in_data1 xor e_hat[2]`

Bits `[7:6]` of the parity-byte error vector are discarded because `out_parity` is 6 bits wide.

When `out_status = 2'b10`, no correction is applied:
- `out_parity = in_parity`
- `out_data0 = in_data0`
- `out_data1 = in_data1`

`popcount()` is the number of `1` bits in the masked operand. All arithmetic is exact integer arithmetic. No saturation occurs.

Relative to a valid codeword, every single-bit error is corrected and every two-bit error is detected with status `2'b10`. With more than two flipped bits, use the syndrome rules above; correction or detection is not guaranteed. Status `2'b00` means zero syndrome, not proof that the received word was unchanged.

## Reset Behavior

The design uses synchronous active-high reset `rst`.

Architectural state: none.

While in reset, and immediately after reset until the first accepted input transaction produces an output transaction:
- `out_valid = 0`
- `out_parity = 6'h00`
- `out_data0 = 8'h00`
- `out_data1 = 8'h00`
- `out_status = 2'b00`

`in_ready` shall be high whenever the block can accept an input transaction. A stateless single-cycle implementation may hold `in_ready = 1` continuously.
