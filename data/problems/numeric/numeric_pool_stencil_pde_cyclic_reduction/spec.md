# CyclicReductionStage

This module performs one non-symmetric 1D cyclic-reduction coarse-operator construction step for a tridiagonal system. Each accepted input transaction identifies one fine-grid center point and provides the three fine-grid stencil coefficients at that point and at its immediate neighbors. The module computes the corresponding coarse-grid west, center, and east coefficients using the cyclic-reduction formulas used for a 3-point non-symmetric operator, including the boundary rule that replaces a zero neighboring diagonal denominator with 1 before division.

## Interface

### RTL interface

```systemverilog
module cyclic_reduction_stage (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a_cw_m1,
    input  logic [63:0]  in_a_cc_m1,
    input  logic [63:0]  in_a_ce_m1,
    input  logic [63:0]  in_a_cw_0,
    input  logic [63:0]  in_a_cc_0,
    input  logic [63:0]  in_a_ce_0,
    input  logic [63:0]  in_a_cw_p1,
    input  logic [63:0]  in_a_cc_p1,
    input  logic [63:0]  in_a_ce_p1,
    input  logic         in_periodic_collapse,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_ac_cw,
    output logic [63:0]  out_ac_cc,
    output logic [63:0]  out_ac_ce
);
```

All 64-bit data ports carry signed two's-complement fixed-point values in Q32.32 format.

### Behavior interface

```python
class CyclicReductionStage:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_a_cw_m1,
            in_a_cc_m1,
            in_a_ce_m1,
            in_a_cw_0,
            in_a_cc_0,
            in_a_ce_0,
            in_a_cw_p1,
            in_a_cc_p1,
            in_a_ce_p1,
            in_periodic_collapse):
        pass
```

The returned dict contains keys `out_ac_cw`, `out_ac_cc`, and `out_ac_ce`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_ac_cc`, `out_ac_ce`, `out_ac_cw`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `cyclic_reduction_stage`: top-level single-transaction kernel implementing one coarse-operator coefficient calculation for one coarse-grid point.

## Functional Behavior

All arithmetic is signed fixed-point Q32.32. A port value `x` represents the real number `to_signed(x, 64) / 2^32`.

The input coefficients correspond to three adjacent fine-grid rows centered on the fine-grid point being eliminated into a coarse-grid point:

- `in_a_cw_m1`, `in_a_cc_m1`, `in_a_ce_m1`: west, center, east coefficients at fine-grid point `i-1`
- `in_a_cw_0`,  `in_a_cc_0`,  `in_a_ce_0`:  west, center, east coefficients at fine-grid point `i`
- `in_a_cw_p1`, `in_a_cc_p1`, `in_a_ce_p1`: west, center, east coefficients at fine-grid point `i+1`

The computation uses the following denominator substitution rule:

- `accm1 = in_a_cc_m1` if `in_a_cc_m1 != 0`
- `accm1 = 1.0` in Q32.32, encoded as `0x0000000100000000`, if `in_a_cc_m1 == 0`
- `accp1 = in_a_cc_p1` if `in_a_cc_p1 != 0`
- `accp1 = 1.0` in Q32.32, encoded as `0x0000000100000000`, if `in_a_cc_p1 == 0`

Let fixed-point multiplication and division be defined as follows for signed 64-bit Q32.32 operands:

- `qmul(a, b) = trunc_toward_zero((a_real * b_real) * 2^32)` returned as signed 64-bit Q32.32
- equivalently on signed integers, if `A = to_signed(a,64)` and `B = to_signed(b,64)`, then `qmul(a,b)` is `trunc_toward_zero((A * B) / 2^32)` wrapped to 64 bits
- `qdiv(a, b) = trunc_toward_zero((a_real / b_real) * 2^32)` returned as signed 64-bit Q32.32
- equivalently on signed integers, if `A = to_signed(a,64)` and `B = to_signed(b,64)`, then `qdiv(a,b)` is `trunc_toward_zero((A * 2^32) / B)` wrapped to 64 bits

`trunc_toward_zero` is signed truncation toward zero, not floor.

The coarse-grid coefficients are computed in this exact operation order:

1. `term_w = qdiv(qmul(in_a_cw_0, in_a_cw_m1), accm1)`
2. `term_c_left = qdiv(qmul(in_a_cw_0, in_a_ce_m1), accm1)`
3. `term_c_right = qdiv(qmul(in_a_ce_0, in_a_cw_p1), accp1)`
4. `term_e = qdiv(qmul(in_a_ce_0, in_a_ce_p1), accp1)`
5. `ac_cw_base = -term_w`
6. `ac_cc_base = in_a_cc_0 - term_c_left - term_c_right`
7. `ac_ce_base = -term_e`

If `in_periodic_collapse == 0`, the output is:

- `out_ac_cw = ac_cw_base`
- `out_ac_cc = ac_cc_base`
- `out_ac_ce = ac_ce_base`

If `in_periodic_collapse == 1`, the output applies the coarse-grid periodic-direction stencil collapse:

1. `collapsed_cc = ac_cc_base + ac_cw_base + ac_ce_base`
2. `collapsed_cw = 0`
3. `collapsed_ce = 0`

and returns:

- `out_ac_cw = collapsed_cw`
- `out_ac_cc = collapsed_cc`
- `out_ac_ce = collapsed_ce`

Every add, subtract, negation, multiply, and divide result is wrapped to 64 bits in two's-complement form before being used by the next step or returned.

Edge cases:

- A neighboring zero diagonal does not cause division by zero; the corresponding denominator is replaced by Q32.32 `1.0`.
- If `in_a_cc_0` is zero, it is used as zero in `ac_cc_base`; only the neighboring denominators are protected.
- Negative operands use signed arithmetic throughout.
- No saturation is applied; all results wrap modulo `2^64`.

## Reset Behavior

The module has no architectural state beyond handshake-related output storage. On active-high synchronous or asynchronous reset, `out_valid` shall be deasserted and `out_ac_cw`, `out_ac_cc`, and `out_ac_ce` shall be driven to `64'd0`. `in_ready` shall indicate the module can accept a new transaction immediately after reset is released.
