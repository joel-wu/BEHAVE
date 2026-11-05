# JPEG Quantize Block Int16

This module quantizes one 8x8 block of already-computed integer DCT coefficients using the integer reciprocal-based quantization algorithm used for 8-bit JPEG sample data. For each of the 64 coefficients, it first derives a reciprocal/correction/shift tuple from the quantization divisor, then computes a rounded integer quotient with sign restored. The block is stateless: one accepted input transaction produces one output transaction containing 64 quantized signed 16-bit coefficients.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_quant_jpeg_quantize_block_int16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_q00,
    input  logic [15:0]  in_q01,
    input  logic [15:0]  in_q02,
    input  logic [15:0]  in_q03,
    input  logic [15:0]  in_q04,
    input  logic [15:0]  in_q05,
    input  logic [15:0]  in_q06,
    input  logic [15:0]  in_q07,
    input  logic [15:0]  in_q08,
    input  logic [15:0]  in_q09,
    input  logic [15:0]  in_q10,
    input  logic [15:0]  in_q11,
    input  logic [15:0]  in_q12,
    input  logic [15:0]  in_q13,
    input  logic [15:0]  in_q14,
    input  logic [15:0]  in_q15,
    input  logic [15:0]  in_q16,
    input  logic [15:0]  in_q17,
    input  logic [15:0]  in_q18,
    input  logic [15:0]  in_q19,
    input  logic [15:0]  in_q20,
    input  logic [15:0]  in_q21,
    input  logic [15:0]  in_q22,
    input  logic [15:0]  in_q23,
    input  logic [15:0]  in_q24,
    input  logic [15:0]  in_q25,
    input  logic [15:0]  in_q26,
    input  logic [15:0]  in_q27,
    input  logic [15:0]  in_q28,
    input  logic [15:0]  in_q29,
    input  logic [15:0]  in_q30,
    input  logic [15:0]  in_q31,
    input  logic [15:0]  in_q32,
    input  logic [15:0]  in_q33,
    input  logic [15:0]  in_q34,
    input  logic [15:0]  in_q35,
    input  logic [15:0]  in_q36,
    input  logic [15:0]  in_q37,
    input  logic [15:0]  in_q38,
    input  logic [15:0]  in_q39,
    input  logic [15:0]  in_q40,
    input  logic [15:0]  in_q41,
    input  logic [15:0]  in_q42,
    input  logic [15:0]  in_q43,
    input  logic [15:0]  in_q44,
    input  logic [15:0]  in_q45,
    input  logic [15:0]  in_q46,
    input  logic [15:0]  in_q47,
    input  logic [15:0]  in_q48,
    input  logic [15:0]  in_q49,
    input  logic [15:0]  in_q50,
    input  logic [15:0]  in_q51,
    input  logic [15:0]  in_q52,
    input  logic [15:0]  in_q53,
    input  logic [15:0]  in_q54,
    input  logic [15:0]  in_q55,
    input  logic [15:0]  in_q56,
    input  logic [15:0]  in_q57,
    input  logic [15:0]  in_q58,
    input  logic [15:0]  in_q59,
    input  logic [15:0]  in_q60,
    input  logic [15:0]  in_q61,
    input  logic [15:0]  in_q62,
    input  logic [15:0]  in_q63,

    input  logic [15:0]  in_w00,
    input  logic [15:0]  in_w01,
    input  logic [15:0]  in_w02,
    input  logic [15:0]  in_w03,
    input  logic [15:0]  in_w04,
    input  logic [15:0]  in_w05,
    input  logic [15:0]  in_w06,
    input  logic [15:0]  in_w07,
    input  logic [15:0]  in_w08,
    input  logic [15:0]  in_w09,
    input  logic [15:0]  in_w10,
    input  logic [15:0]  in_w11,
    input  logic [15:0]  in_w12,
    input  logic [15:0]  in_w13,
    input  logic [15:0]  in_w14,
    input  logic [15:0]  in_w15,
    input  logic [15:0]  in_w16,
    input  logic [15:0]  in_w17,
    input  logic [15:0]  in_w18,
    input  logic [15:0]  in_w19,
    input  logic [15:0]  in_w20,
    input  logic [15:0]  in_w21,
    input  logic [15:0]  in_w22,
    input  logic [15:0]  in_w23,
    input  logic [15:0]  in_w24,
    input  logic [15:0]  in_w25,
    input  logic [15:0]  in_w26,
    input  logic [15:0]  in_w27,
    input  logic [15:0]  in_w28,
    input  logic [15:0]  in_w29,
    input  logic [15:0]  in_w30,
    input  logic [15:0]  in_w31,
    input  logic [15:0]  in_w32,
    input  logic [15:0]  in_w33,
    input  logic [15:0]  in_w34,
    input  logic [15:0]  in_w35,
    input  logic [15:0]  in_w36,
    input  logic [15:0]  in_w37,
    input  logic [15:0]  in_w38,
    input  logic [15:0]  in_w39,
    input  logic [15:0]  in_w40,
    input  logic [15:0]  in_w41,
    input  logic [15:0]  in_w42,
    input  logic [15:0]  in_w43,
    input  logic [15:0]  in_w44,
    input  logic [15:0]  in_w45,
    input  logic [15:0]  in_w46,
    input  logic [15:0]  in_w47,
    input  logic [15:0]  in_w48,
    input  logic [15:0]  in_w49,
    input  logic [15:0]  in_w50,
    input  logic [15:0]  in_w51,
    input  logic [15:0]  in_w52,
    input  logic [15:0]  in_w53,
    input  logic [15:0]  in_w54,
    input  logic [15:0]  in_w55,
    input  logic [15:0]  in_w56,
    input  logic [15:0]  in_w57,
    input  logic [15:0]  in_w58,
    input  logic [15:0]  in_w59,
    input  logic [15:0]  in_w60,
    input  logic [15:0]  in_w61,
    input  logic [15:0]  in_w62,
    input  logic [15:0]  in_w63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00,
    output logic [15:0]  out_c01,
    output logic [15:0]  out_c02,
    output logic [15:0]  out_c03,
    output logic [15:0]  out_c04,
    output logic [15:0]  out_c05,
    output logic [15:0]  out_c06,
    output logic [15:0]  out_c07,
    output logic [15:0]  out_c08,
    output logic [15:0]  out_c09,
    output logic [15:0]  out_c10,
    output logic [15:0]  out_c11,
    output logic [15:0]  out_c12,
    output logic [15:0]  out_c13,
    output logic [15:0]  out_c14,
    output logic [15:0]  out_c15,
    output logic [15:0]  out_c16,
    output logic [15:0]  out_c17,
    output logic [15:0]  out_c18,
    output logic [15:0]  out_c19,
    output logic [15:0]  out_c20,
    output logic [15:0]  out_c21,
    output logic [15:0]  out_c22,
    output logic [15:0]  out_c23,
    output logic [15:0]  out_c24,
    output logic [15:0]  out_c25,
    output logic [15:0]  out_c26,
    output logic [15:0]  out_c27,
    output logic [15:0]  out_c28,
    output logic [15:0]  out_c29,
    output logic [15:0]  out_c30,
    output logic [15:0]  out_c31,
    output logic [15:0]  out_c32,
    output logic [15:0]  out_c33,
    output logic [15:0]  out_c34,
    output logic [15:0]  out_c35,
    output logic [15:0]  out_c36,
    output logic [15:0]  out_c37,
    output logic [15:0]  out_c38,
    output logic [15:0]  out_c39,
    output logic [15:0]  out_c40,
    output logic [15:0]  out_c41,
    output logic [15:0]  out_c42,
    output logic [15:0]  out_c43,
    output logic [15:0]  out_c44,
    output logic [15:0]  out_c45,
    output logic [15:0]  out_c46,
    output logic [15:0]  out_c47,
    output logic [15:0]  out_c48,
    output logic [15:0]  out_c49,
    output logic [15:0]  out_c50,
    output logic [15:0]  out_c51,
    output logic [15:0]  out_c52,
    output logic [15:0]  out_c53,
    output logic [15:0]  out_c54,
    output logic [15:0]  out_c55,
    output logic [15:0]  out_c56,
    output logic [15:0]  out_c57,
    output logic [15:0]  out_c58,
    output logic [15:0]  out_c59,
    output logic [15:0]  out_c60,
    output logic [15:0]  out_c61,
    output logic [15:0]  out_c62,
    output logic [15:0]  out_c63
);
```

### Behavior interface

```python
class CompressionJpegQuantJpegQuantizeBlockInt16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_q00, in_q01, in_q02, in_q03, in_q04, in_q05, in_q06, in_q07,
        in_q08, in_q09, in_q10, in_q11, in_q12, in_q13, in_q14, in_q15,
        in_q16, in_q17, in_q18, in_q19, in_q20, in_q21, in_q22, in_q23,
        in_q24, in_q25, in_q26, in_q27, in_q28, in_q29, in_q30, in_q31,
        in_q32, in_q33, in_q34, in_q35, in_q36, in_q37, in_q38, in_q39,
        in_q40, in_q41, in_q42, in_q43, in_q44, in_q45, in_q46, in_q47,
        in_q48, in_q49, in_q50, in_q51, in_q52, in_q53, in_q54, in_q55,
        in_q56, in_q57, in_q58, in_q59, in_q60, in_q61, in_q62, in_q63,
        in_w00, in_w01, in_w02, in_w03, in_w04, in_w05, in_w06, in_w07,
        in_w08, in_w09, in_w10, in_w11, in_w12, in_w13, in_w14, in_w15,
        in_w16, in_w17, in_w18, in_w19, in_w20, in_w21, in_w22, in_w23,
        in_w24, in_w25, in_w26, in_w27, in_w28, in_w29, in_w30, in_w31,
        in_w32, in_w33, in_w34, in_w35, in_w36, in_w37, in_w38, in_w39,
        in_w40, in_w41, in_w42, in_w43, in_w44, in_w45, in_w46, in_w47,
        in_w48, in_w49, in_w50, in_w51, in_w52, in_w53, in_w54, in_w55,
        in_w56, in_w57, in_w58, in_w59, in_w60, in_w61, in_w62, in_w63
    ):
        pass
```

Each `process` call represents one accepted block and returns one dictionary
with exactly the 64 keys `out_c00` through `out_c63`, using two-digit decimal
suffixes. Arguments and results are unsigned 16-bit words: coefficients and
results encode signed two's-complement integers, while divisors are unsigned.
The result is not a packed vector, list, or `None`; an all-zero block still
returns all 64 fields. No state persists between calls. Construction and
`reset()` have no numerical effect, and `reset()` returns `None`. No calls
are made for idle or output-stall cycles.

## Modules

- `compression_jpeg_quant_jpeg_quantize_block_int16`: top-level quantization block for one 8x8 coefficient block.

## Functional Behavior

Each input transaction contains 64 signed 16-bit DCT coefficients `in_q00..in_q63` and 64 unsigned 16-bit quantization divisors `in_w00..in_w63`, both in normal 8x8 raster order flattened to indices 0..63.

For each coefficient index `i` from 0 to 63, the output coefficient `out_ci` is computed independently from the pair `(qi, wi)` at the same index.

Interpretation of ports:
- `qi` is a signed 16-bit two's-complement integer.
- `wi` is an unsigned 16-bit integer.
- `out_ci` is a signed 16-bit two's-complement integer returned on the port as a 16-bit bit pattern.

For each `wi`, derive a reciprocal tuple `(recip, corr, shift)` using the following algorithm. All quantities in this derivation are integer-valued. `flss(x)` is the position of the highest set bit in a 16-bit unsigned integer, numbered 1 for bit 0 through 16 for bit 15. `flss(0) = 0`.

1. If `wi <= 1`, clamp to the identity divisor case:
   - `recip = 1`
   - `corr = 0`
   - `shift = -16`

2. Otherwise:
   - `b = flss(wi) - 1`
   - `r = 16 + b`
   - `fq = floor(2^r / wi)`
   - `fr = 2^r - fq * wi`
   - `corr = floor(wi / 2)`

   Then apply exactly one of these three cases:
   - If `fr == 0`:
     - `fq = floor(fq / 2)`
     - `r = r - 1`
   - Else if `fr <= floor(wi / 2)`:
     - `corr = corr + 1`
   - Else:
     - `fq = fq + 1`

   Finally:
   - `recip = fq`
   - `shift = r - 16`

For each coefficient, quantize with symmetric round-to-nearest and sign restoration:

1. Let `mag = abs(qi)`.
2. Compute `product = (mag + corr) * recip` as an unsigned integer wide enough to hold the exact product.
3. Let `s = shift + 16`.
4. Compute `quot = floor(product / 2^s)`. Since `shift` is `-16` only in the `wi <= 1` case, `s` is never negative.
5. If `qi < 0`, negate the quotient: `result = -quot`. Otherwise `result = quot`.

`out_ci` is `result` truncated to 16 bits and emitted as a two's-complement bit pattern. No saturation is applied. Under legal JPEG operating ranges, the result fits in signed 16 bits.

The `wi <= 1` case is defined to behave as an identity quantizer:
- `quot = abs(qi)`
- `result = qi`

This follows directly from `recip = 1`, `corr = 0`, and `shift = -16`.

`flss(x)` shall be implemented by fixed-width 16-bit highest-set-bit detection:
- `flss(0) = 0`
- `flss(1) = 1`
- `flss(2) = 2`
- `flss(3) = 2`
- `flss(0x8000) = 16`

Worked example for one coefficient:
- `qi = -25`, `wi = 10`
- `flss(10) = 4`, so `b = 3`, `r = 19`
- `fq = floor(524288 / 10) = 52428`
- `fr = 524288 - 52428*10 = 8`
- `corr = floor(10/2) = 5`
- Since `fr > 5`, set `fq = 52429`
- `recip = 52429`, `shift = 19 - 16 = 3`
- `mag = 25`
- `product = (25 + 5) * 52429 = 1572870`
- `s = 3 + 16 = 19`
- `quot = floor(1572870 / 524288) = 3`
- Sign restore gives `result = -3`, so output bit pattern is `16'hFFFD`.

The block has no architectural state affecting the numeric result from one transaction to the next.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

After reset, the block is idle with no queued transaction. `out_valid` shall be 0 until a post-reset input transaction is accepted and an output transaction is available. `in_ready` may be 1 immediately after reset. Output data ports `out_c00..out_c63` have no required value while `out_valid` is 0.
