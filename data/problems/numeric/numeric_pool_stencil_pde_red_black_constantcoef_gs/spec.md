# RedBlackStencilPointUpdate

RedBlackStencilPointUpdate performs one red/black Gauss-Seidel point relaxation update for a single grid point of a structured linear system. The kernel supports 1D, 2D, and 3D constant off-diagonal stencil coefficients with stencil sizes 3, 5, or 7, and supports either a constant diagonal coefficient or a per-point diagonal input. Each accepted input transaction represents one candidate grid point; the kernel determines from the point coordinates and the current red/black phase whether that point is active in the current sweep, optionally applies the zero-initial-guess initialization rule, computes the updated solution value by subtracting weighted neighbor values from the right-hand side, dividing by the diagonal coefficient, and returns the new point value together with the next red/black phase bit.

## Interface

### RTL interface

```systemverilog
module red_black_stencil_point_update (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_zero_guess,
    input  logic [1:0]  in_phase_mode,
    input  logic        in_rb_phase,
    input  logic [1:0]  in_ndim,
    input  logic [2:0]  in_stencil_size,
    input  logic        in_constant_diag,
    input  logic [31:0] in_b,
    input  logic [31:0] in_xm,
    input  logic [31:0] in_xp,
    input  logic [31:0] in_ym,
    input  logic [31:0] in_yp,
    input  logic [31:0] in_zm,
    input  logic [31:0] in_zp,
    input  logic [31:0] in_app0,
    input  logic [31:0] in_app1,
    input  logic [31:0] in_app2,
    input  logic [31:0] in_app3,
    input  logic [31:0] in_app4,
    input  logic [31:0] in_app5,
    input  logic [31:0] in_ap_const,
    input  logic [31:0] in_ap_var,
    input  logic [15:0] in_coord_x,
    input  logic [15:0] in_coord_y,
    input  logic [15:0] in_coord_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_active,
    output logic [31:0] out_x_new,
    output logic        out_rb_phase_next
);
```

### Behavior interface

```python
class RedBlackStencilPointUpdate:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_zero_guess,
        in_phase_mode,
        in_rb_phase,
        in_ndim,
        in_stencil_size,
        in_constant_diag,
        in_b,
        in_xm,
        in_xp,
        in_ym,
        in_yp,
        in_zm,
        in_zp,
        in_app0,
        in_app1,
        in_app2,
        in_app3,
        in_app4,
        in_app5,
        in_ap_const,
        in_ap_var,
        in_coord_x,
        in_coord_y,
        in_coord_z,
    ):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_active`, `out_rb_phase_next`: 1.
- `out_x_new`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `red_black_stencil_point_update`: top-level single-point red/black Gauss-Seidel update kernel.

## Functional Behavior

All 32-bit data ports carry signed two's-complement integers, with no implicit fractional rescaling. Products are formed in signed arithmetic, accumulated in signed arithmetic, and the final division is signed truncation toward zero. Returned `out_x_new` is the 32-bit two's-complement representation of the signed result modulo 2^32.

`in_phase_mode` selects one of two update modes:

- `2'b00`: zero-initial-guess initialization mode.
- `2'b01`: regular red/black relaxation mode.
- Other encodings are treated identically to `2'b01`.

The caller supplies this already-decoded phase; `in_zero_guess` is retained as metadata and does not override `in_phase_mode` or affect the result. Grid-level sweep scheduling is outside this point kernel.

`in_ndim` selects the number of spatial dimensions:

- `2'b01`: 1D.
- `2'b10`: 2D.
- `2'b11`: 3D.
- `2'b00` is treated as 1D.

`in_stencil_size` selects the number of stencil entries:

- `3`: 1D three-point stencil.
- `5`: 2D five-point stencil.
- `7`: 3D seven-point stencil.

For any other `in_stencil_size`, the kernel behaves as if `in_stencil_size == 3`.

The red/black activity of the current point is determined from the current sweep phase and the point coordinates exactly as follows:

1. Interpret `in_coord_x`, `in_coord_y`, and `in_coord_z` as unsigned 16-bit integers.
2. Form `coord_sum` as:
   - 1D: `coord_x`
   - 2D: `coord_x + coord_y`
   - 3D: `coord_x + coord_y + coord_z`
3. Form `redblack = (in_rb_phase + coord_sum) mod 2`.
4. The point is active for update iff `redblack == 0`.

`out_active` is `1` when the point is active and `0` otherwise.

`out_rb_phase_next` equals `in_rb_phase ^ 1'b1` for every accepted input transaction.

If `out_active == 0`, no relaxation is applied and `out_x_new` is `0`.

If `out_active == 1` and `in_phase_mode == 2'b00`, the zero-initial-guess initialization rule is applied:

- Select the diagonal coefficient `diag`:
  - if `in_constant_diag == 1`, `diag = signed(in_ap_const)`
  - else `diag = signed(in_ap_var)`
- Compute `x_new = trunc_zero( signed(in_b) / diag )`.
- `out_x_new` is `x_new` converted to 32-bit two's-complement.

If `out_active == 1` and `in_phase_mode != 2'b00`, the regular red/black relaxation rule is applied:

1. Select the diagonal coefficient `diag`:
   - if `in_constant_diag == 1`, `diag = signed(in_ap_const)`
   - else `diag = signed(in_ap_var)`

2. Compute the off-diagonal weighted neighbor sum in this exact term order.

For `in_stencil_size == 3`:
- `neighbor_sum = signed(in_app0) * signed(in_xm) + signed(in_app1) * signed(in_xp)`

For `in_stencil_size == 5`:
- `neighbor_sum = signed(in_app0) * signed(in_xm) + signed(in_app1) * signed(in_xp) + signed(in_app2) * signed(in_ym) + signed(in_app3) * signed(in_yp)`

For `in_stencil_size == 7`:
- `neighbor_sum = signed(in_app0) * signed(in_xm) + signed(in_app1) * signed(in_xp) + signed(in_app2) * signed(in_ym) + signed(in_app3) * signed(in_yp) + signed(in_app4) * signed(in_zm) + signed(in_app5) * signed(in_zp)`

For any other `in_stencil_size`, use the `in_stencil_size == 3` rule.

3. Compute `numerator = signed(in_b) - neighbor_sum`.

4. Compute `x_new = trunc_zero( numerator / diag )`.

5. `out_x_new` is `x_new` converted to 32-bit two's-complement.

Division by zero is defined explicitly to keep the transaction total: if the selected `diag` is zero, `out_x_new` is `0` regardless of `in_phase_mode`, stencil size, or numerator.

The kernel is 1:1: every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high. On reset, all architectural state is set to zero. The only architectural state is the registered output channel contents. After reset and before the first accepted input transaction:

- `out_valid = 0`
- `out_active = 0`
- `out_x_new = 32'h00000000`
- `out_rb_phase_next = 0`

The kernel has no persistent algorithmic state across transactions.
