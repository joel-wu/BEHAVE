# Semi Interpolation 1D Fine-Point Kernel

This module performs one 1D semi-coarsened interpolation update for a single fine-grid point. The kernel implements the fine-point step of structured-grid interpolation: coarse-grid values are injected directly at coarse locations, and each fine location midway between two neighboring coarse points is computed as a two-tap weighted sum of those neighboring coarse-point values. One accepted input transaction computes one fine-point output using two scalar interpolation coefficients and two scalar neighboring values.

## Interface

### RTL interface

```systemverilog
module semi_interp_1d_fine_kernel (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_p0,
    input  logic [31:0] in_p1,
    input  logic [31:0] in_e_left,
    input  logic [31:0] in_e_right,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_e
);
```

### Behavior interface

```python
class SemiInterp1DFineKernel:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_p0, in_p1, in_e_left, in_e_right):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_e`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `semi_interp_1d_fine_kernel`: top-level single-point interpolation kernel.

## Functional Behavior

Each input port carries a 32-bit two's-complement signed integer.

One accepted input transaction computes one output transaction. There is no accumulation across transactions and no dependence on prior input data.

The kernel computes a weighted sum of the left and right neighboring coarse-point values:

- Let `p0` be `in_p0` interpreted as signed 32-bit.
- Let `p1` be `in_p1` interpreted as signed 32-bit.
- Let `e_left` be `in_e_left` interpreted as signed 32-bit.
- Let `e_right` be `in_e_right` interpreted as signed 32-bit.

The mathematical result before truncation is:

- `sum64 = (p0 * e_left) + (p1 * e_right)`

The multiplications are signed 32-bit by signed 32-bit, and the addition is performed on the full mathematical product values. The final output is the low 32 bits of `sum64`.

The output port value is:

- `out_e = sum64 mod 2^32`

Equivalently, the hardware may perform two signed multiplications, a signed addition, and then return the 32-bit two's-complement bit pattern of the result.

Operation order is fixed as written:

1. Multiply `p0` by `e_left`.
2. Multiply `p1` by `e_right`.
3. Add the two products.
4. Truncate to 32 bits by discarding all bits above bit 31.

No saturation, rounding, or overflow detection is performed.

Edge-case behavior:
- Negative operands use standard two's-complement signed interpretation.
- If either product or the final sum exceeds the signed 32-bit range, the result wraps modulo `2^32`.
- All `0x00000000` through `0xFFFFFFFF` input bit patterns are valid and are interpreted only by signed two's-complement arithmetic.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

After reset, the module is idle and contains no retained data from earlier transactions.

Output and handshake requirements immediately after reset deassertion:
- `in_ready = 1`
- `out_valid = 0`
- `out_e = 32'h00000000`

Because the kernel is stateless, every accepted input after reset is processed according to the Functional Behavior section with no dependence on reset history.
