# Kyber Poly FromBytes Pair

This block unpacks one 3-byte group from the Kyber polynomial serialization layout into two unsigned 12-bit coefficients. It accepts every 24-bit input, including encodings of coefficients greater than or equal to 3329, without modular reduction or canonicality checking. A full 256-coefficient polynomial consists of 128 such groups, but this block neither assembles nor tracks polynomial frames.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_poly_frombytes_pair (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_packed,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_coeff0,
    output logic [11:0] out_coeff1
);
```

### Behavior interface

```python
class KyberPolyFrombytesPair:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_packed):
        pass
```

The `process()` return value is a dict with keys `out_coeff0` and `out_coeff1`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_coeff0`, `out_coeff1`: 12.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cryptography_pqc_kyber_kyber_poly_frombytes_pair`: top-level streaming unpacker for one 24-bit packed coefficient pair.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Constants:
- `KYBER_N = 256`.
- Each polynomial coefficient is represented here as an unsigned 12-bit integer in the range `[0, 4095]`.
- One full Kyber polynomial occupies `KYBER_N / 2 = 128` packed groups, each group containing 2 coefficients in 3 bytes.

Input interpretation:
- `in_packed[7:0]` is byte `b0`.
- `in_packed[15:8]` is byte `b1`.
- `in_packed[23:16]` is byte `b2`.

For each accepted input transaction, the block computes exactly one output transaction as follows:

1. Extract the three input bytes:
   - `b0 = in_packed[7:0]`
   - `b1 = in_packed[15:8]`
   - `b2 = in_packed[23:16]`

2. Decode the first coefficient:
   - Form the 16-bit intermediate `w0 = b0 | (b1 << 8)`.
   - Compute `coeff0 = w0 & 12'hFFF`.
   - Equivalently, `coeff0` is the low 12 bits of the concatenation `{b1, b0}`.

3. Decode the second coefficient:
   - Form the 16-bit intermediate `w1 = (b1 >> 4) | (b2 << 4)`.
   - Compute `coeff1 = w1 & 12'hFFF`.
   - Equivalently, `coeff1` is the low 12 bits of the value whose low 4 bits are `b1[7:4]` and whose high 8 bits are `b2[7:0]`.

4. Drive the output transaction:
   - `out_coeff0 = coeff0`
   - `out_coeff1 = coeff1`

Bit and arithmetic rules:
- All quantities are unsigned.
- `>>` is logical right shift.
- `<<` is left shift with zero fill.
- Masking with `12'hFFF` truncates to 12 bits modulo `2^12`.
- Because `b0`, `b1`, and `b2` are 8-bit values, the decoded results are always in `[0, 4095]`.

Transaction ordering:
- The block is stateless with respect to polynomial position. It does not track which of the 128 packed groups within a polynomial is being processed.
- The environment is responsible for presenting packed groups in the desired order.
- The nth accepted input transaction produces the decoded coefficient pair for the nth presented packed group.

Worked example:
- If `in_packed = 24'hCDEFAB`, then `b0 = 8'hAB`, `b1 = 8'hEF`, `b2 = 8'hCD`.
- `coeff0 = ((16'h00AB) | (16'h00EF << 8)) & 16'h0FFF = 16'h0FAB`.
- `coeff1 = ((16'h00EF >> 4) | (16'h00CD << 4)) & 16'h0FFF = 16'h0CDE`.
- Output is `out_coeff0 = 12'hFAB`, `out_coeff1 = 12'hCDE`.

## Reset Behavior

The block has no architectural state.

Reset is synchronous active-high `rst`.

While `rst = 1`:
- `in_ready = 1`
- `out_valid = 0`
- `out_coeff0 = 12'h000`
- `out_coeff1 = 12'h000`

After reset is deasserted:
- The block remains stateless.
- No retained state exists from before or during reset.
