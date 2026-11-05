# AES128 Round

This module performs one AES-128 encryption round on a 128-bit state using a 128-bit round key. The round function is the standard AES forward round structure: SubBytes, ShiftRows, MixColumns, then AddRoundKey. The computation is for the non-final round only; MixColumns is always applied. The block is stateless and accepts one input transaction per output transaction.

## Interface

### RTL interface

```systemverilog
module aes128_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,
    input  logic [127:0] in_round_key,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);
```

### Behavior interface

```python
class AES128Round:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_state, in_round_key):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_state`: 128.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `aes128_round`: top-level module implementing one forward AES-128 non-final round.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

`in_state` and `in_round_key` are 128-bit unsigned values. Each is interpreted as 16 bytes in big-endian byte order:

- `state_byte[0] = in_state[127:120]`
- `state_byte[1] = in_state[119:112]`
- ...
- `state_byte[15] = in_state[7:0]`

- `round_key_byte[0] = in_round_key[127:120]`
- `round_key_byte[1] = in_round_key[119:112]`
- ...
- `round_key_byte[15] = in_round_key[7:0]`

The 16 state bytes are arranged as the AES state matrix in column-major order:

- row 0: `state_byte[0],  state_byte[4],  state_byte[8],  state_byte[12]`
- row 1: `state_byte[1],  state_byte[5],  state_byte[9],  state_byte[13]`
- row 2: `state_byte[2],  state_byte[6],  state_byte[10], state_byte[14]`
- row 3: `state_byte[3],  state_byte[7],  state_byte[11], state_byte[15]`

The output is computed in this order:

1. **SubBytes**

   Apply the AES forward S-box independently to each input byte. To define the complete mapping, let `a` be the multiplicative inverse of the byte in GF(2^8) modulo `0x11B`, with the inverse of zero defined as zero. Number bits from 0 (least significant) to 7. Output bit `i` is `a[i] XOR a[(i+4) mod 8] XOR a[(i+5) mod 8] XOR a[(i+6) mod 8] XOR a[(i+7) mod 8] XOR (0x63)[i]`. Examples:

   - `SBOX[0x00] = 0x63`
   - `SBOX[0x53] = 0xED`
   - `SBOX[0xFF] = 0x16`

   Let the substituted bytes be `sub_byte[0..15]`.

2. **ShiftRows**

   Interpret `sub_byte[0..15]` as a 4x4 AES state in the same column-major layout. Rotate each row left by its row index:

   - row 0: no rotation
   - row 1: rotate left by 1 byte
   - row 2: rotate left by 2 bytes
   - row 3: rotate left by 3 bytes

   In byte-index form, the shifted state bytes are:

   - `shift_byte[0]  = sub_byte[0]`
   - `shift_byte[1]  = sub_byte[5]`
   - `shift_byte[2]  = sub_byte[10]`
   - `shift_byte[3]  = sub_byte[15]`
   - `shift_byte[4]  = sub_byte[4]`
   - `shift_byte[5]  = sub_byte[9]`
   - `shift_byte[6]  = sub_byte[14]`
   - `shift_byte[7]  = sub_byte[3]`
   - `shift_byte[8]  = sub_byte[8]`
   - `shift_byte[9]  = sub_byte[13]`
   - `shift_byte[10] = sub_byte[2]`
   - `shift_byte[11] = sub_byte[7]`
   - `shift_byte[12] = sub_byte[12]`
   - `shift_byte[13] = sub_byte[1]`
   - `shift_byte[14] = sub_byte[6]`
   - `shift_byte[15] = sub_byte[11]`

3. **MixColumns**

   Operate independently on each of the four columns of `shift_byte`. For a column input `(a0, a1, a2, a3)`, the output `(m0, m1, m2, m3)` is:

   - `m0 = mul2(a0) ^ mul3(a1) ^ a2 ^ a3`
   - `m1 = a0 ^ mul2(a1) ^ mul3(a2) ^ a3`
   - `m2 = a0 ^ a1 ^ mul2(a2) ^ mul3(a3)`
   - `m3 = mul3(a0) ^ a1 ^ a2 ^ mul2(a3)`

   where all quantities are 8-bit values and `^` is bitwise XOR.

   Finite-field multiplication is in GF(2^8) with AES reduction polynomial `x^8 + x^4 + x^3 + x + 1`, implemented as:

   - `mul2(x) = ((x << 1) & 0xFF) ^ 0x1B` if `x[7] = 1`
   - `mul2(x) = ((x << 1) & 0xFF)` if `x[7] = 0`
   - `mul3(x) = mul2(x) ^ x`

   Columns are:
   - column 0: bytes `(shift_byte[0], shift_byte[1], shift_byte[2], shift_byte[3])`
   - column 1: bytes `(shift_byte[4], shift_byte[5], shift_byte[6], shift_byte[7])`
   - column 2: bytes `(shift_byte[8], shift_byte[9], shift_byte[10], shift_byte[11])`
   - column 3: bytes `(shift_byte[12], shift_byte[13], shift_byte[14], shift_byte[15])`

   Let the mixed bytes be `mix_byte[0..15]`, preserving column-major byte numbering.

4. **AddRoundKey**

   XOR each mixed byte with the corresponding round-key byte:

   - `out_byte[i] = mix_byte[i] ^ round_key_byte[i]` for `i = 0..15`

5. **Output packing**

   `out_state` is the 128-bit unsigned value formed by concatenating `out_byte[0]` through `out_byte[15]` in the same big-endian order:

   - `out_state[127:120] = out_byte[0]`
   - ...
   - `out_state[7:0] = out_byte[15]`

All byte results are exact 8-bit values. No saturation occurs. All XORs are bitwise modulo-2. The block has no special cases; every 128-bit input pair maps to exactly one 128-bit output.

## Reset Behavior

Reset is synchronous active-high. The block has no architectural state.

After reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_state = 128'h00000000000000000000000000000000`

When `out_valid = 0`, the output payload is otherwise unspecified. When `out_valid = 1` and `out_ready = 0`, the pending output must remain stable until consumed or cancelled by reset. Reset clears transport state even though the arithmetic mapping is stateless.
