# Q15 Vector Scale Element

This module implements one element of the CMSIS-DSP Q15 scaling operation. Each accepted input transaction supplies one signed Q15 sample, one signed Q15 scale factor, and one signed shift value. The module multiplies the two Q15 operands to form a signed 2.30 intermediate, then applies a power-of-two scaling by shifting the product by `shift` bits relative to the Q15 point, equivalent to an arithmetic right shift by `15 - shift`, and finally saturates the result to signed 1.15 format.

## Interface

### RTL interface

```systemverilog
module q15_vector_scale_element (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,
    input  logic [15:0] in_scale_fract,
    input  logic [7:0]  in_shift,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);
```

### Behavior interface

```python
class Q15VectorScaleElement:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample, in_scale_fract, in_shift):
        pass
```

Each call represents one accepted input transaction. The sample and scale
arguments are unsigned 16-bit integers encoding signed Q15 values; `in_shift`
is an unsigned 8-bit integer encoding its signed shift. Return one dictionary
with exactly the key `out_sample`:

```python
{"out_sample": ...}
```

The value is an unsigned 16-bit integer encoding the saturated signed result.
Every input, including an extreme shift or zero product, returns this shape,
not `None` or a list. Results do not depend on earlier requests. `reset()`
restores the reset output and returns `None` without producing a response.

## Modules

- `q15_vector_scale_element`: top-level module that accepts one Q15 input sample per transaction and produces one Q15 output sample per transaction.

## Functional Behavior

For each accepted input transaction, the module produces exactly one output transaction.

`in_sample` and `in_scale_fract` are 16-bit two's-complement signed integers interpreted as Q15 values. `in_shift` is an 8-bit two's-complement signed integer.

Let:

- `sample = signed16(in_sample)`
- `scale_fract = signed16(in_scale_fract)`
- `shift = signed8(in_shift)`

The computation is:

1. Form the signed product
   - `product = sample * scale_fract`
   - `product` is mathematically exact and corresponds to a Q30 fixed-point value.

2. Compute the post-multiply shift amount
   - `k_shift = 15 - shift`

3. Shift the product arithmetically by `k_shift`
   - if `k_shift >= 0`, compute `shifted = product >> k_shift` using arithmetic right shift
   - if `k_shift < 0`, compute `shifted = product << (-k_shift)`

The shift is a pure arithmetic shift. There is no rounding offset or round-to-nearest step. Right shifts truncate toward negative infinity, exactly as an arithmetic signed shift on a two's-complement value.

4. Saturate to signed 16-bit range
   - if `shifted > 32767`, output `32767`
   - else if `shifted < -32768`, output `-32768`
   - else output `shifted`

5. Drive the saturated result on `out_sample` as a 16-bit two's-complement bit pattern.

Intermediate shifts operate on mathematical integers without wraparound; saturation is applied only to the final result.

Worked examples:

- `in_sample = 0x4000` (`+0.5`), `in_scale_fract = 0x4000` (`+0.5`), `in_shift = 0`
  - `product = 16384 * 16384 = 268435456`
  - `k_shift = 15`
  - `shifted = 268435456 >> 15 = 8192`
  - output `0x2000`

- `in_sample = 0x8000` (`-1.0`), `in_scale_fract = 0x8000` (`-1.0`), `in_shift = 1`
  - `product = (-32768) * (-32768) = 1073741824`
  - `k_shift = 14`
  - `shifted = 1073741824 >> 14 = 65536`
  - saturated output `0x7FFF`

- `in_sample = 0xFFFF` (`-1/32768`), `in_scale_fract = 0x4000`, `in_shift = 0`
  - `product = -16384`
  - `k_shift = 15`
  - `shifted = -1` by arithmetic right shift
  - output `0xFFFF`

The legal input domain includes all 16-bit patterns on `in_sample` and `in_scale_fract` and all 8-bit patterns on `in_shift`. Behavior is defined for the full domain. For very large positive or negative left shifts, the mathematical result is still the shifted integer followed by 16-bit saturation.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst = 1`, the architectural state is set to:

- `out_sample = 16'h0000`

There is no other architectural state required by the functional contract.

After reset, `out_valid` is low and `out_sample` remains zero until a post-reset input is accepted. A computed result may be presented before the downstream accepts it; while `out_valid && !out_ready`, its payload and valid signal remain stable. Reset discards any pending result.
