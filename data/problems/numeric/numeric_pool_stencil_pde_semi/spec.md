# Semi Interp Assemble Box Projector

This is the single-box ghost-growth and coarsening-lattice geometry kernel from semi-coarsened interpolation assembly, not its distributed communication scheduler. For each accepted input transaction, it first grows the fine-grid box by a per-dimension ghost width, then maps the grown box from fine coordinates into coarse coordinates using the supplied coarsening index and stride. The same projected coarse box is emitted on four output box groups corresponding to send, receive, send-reference, and receive-reference box lists.

## Interface

### RTL interface

```systemverilog
module semi_interp_assemble_box_projector (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_dim,
    input  logic        in_p_stored_as_transpose,
    input  logic [1:0]  in_cdir,
    input  logic signed [31:0] in_index_x,
    input  logic signed [31:0] in_index_y,
    input  logic signed [31:0] in_index_z,
    input  logic signed [31:0] in_stride_x,
    input  logic signed [31:0] in_stride_y,
    input  logic signed [31:0] in_stride_z,
    input  logic signed [31:0] in_fine_imin_x,
    input  logic signed [31:0] in_fine_imin_y,
    input  logic signed [31:0] in_fine_imin_z,
    input  logic signed [31:0] in_fine_imax_x,
    input  logic signed [31:0] in_fine_imax_y,
    input  logic signed [31:0] in_fine_imax_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [31:0] out_send_imin_x,
    output logic signed [31:0] out_send_imin_y,
    output logic signed [31:0] out_send_imin_z,
    output logic signed [31:0] out_send_imax_x,
    output logic signed [31:0] out_send_imax_y,
    output logic signed [31:0] out_send_imax_z,
    output logic signed [31:0] out_recv_imin_x,
    output logic signed [31:0] out_recv_imin_y,
    output logic signed [31:0] out_recv_imin_z,
    output logic signed [31:0] out_recv_imax_x,
    output logic signed [31:0] out_recv_imax_y,
    output logic signed [31:0] out_recv_imax_z,
    output logic signed [31:0] out_sendr_imin_x,
    output logic signed [31:0] out_sendr_imin_y,
    output logic signed [31:0] out_sendr_imin_z,
    output logic signed [31:0] out_sendr_imax_x,
    output logic signed [31:0] out_sendr_imax_y,
    output logic signed [31:0] out_sendr_imax_z,
    output logic signed [31:0] out_recvr_imin_x,
    output logic signed [31:0] out_recvr_imin_y,
    output logic signed [31:0] out_recvr_imin_z,
    output logic signed [31:0] out_recvr_imax_x,
    output logic signed [31:0] out_recvr_imax_y,
    output logic signed [31:0] out_recvr_imax_z
);
```

### Behavior interface

```python
class SemiInterpAssembleBoxProjector:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_dim,
            in_p_stored_as_transpose,
            in_cdir,
            in_index_x,
            in_index_y,
            in_index_z,
            in_stride_x,
            in_stride_y,
            in_stride_z,
            in_fine_imin_x,
            in_fine_imin_y,
            in_fine_imin_z,
            in_fine_imax_x,
            in_fine_imax_y,
            in_fine_imax_z):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_recv_imax_x`, `out_recv_imax_y`, `out_recv_imax_z`, `out_recv_imin_x`,
  `out_recv_imin_y`, `out_recv_imin_z`, `out_recvr_imax_x`, `out_recvr_imax_y`,
  `out_recvr_imax_z`, `out_recvr_imin_x`, `out_recvr_imin_y`, `out_recvr_imin_z`,
  `out_send_imax_x`, `out_send_imax_y`, `out_send_imax_z`, `out_send_imin_x`,
  `out_send_imin_y`, `out_send_imin_z`, `out_sendr_imax_x`, `out_sendr_imax_y`,
  `out_sendr_imax_z`, `out_sendr_imin_x`, `out_sendr_imin_y`, `out_sendr_imin_z`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `semi_interp_assemble_box_projector`: top-level kernel. Computes one projected coarse box from one fine box and replicates that projected box onto the four output box groups.

## Functional Behavior

Each accepted input transaction produces exactly one output transaction.

All coordinate, index, stride, and box endpoint ports are signed 32-bit integers interpreted in two's-complement form.

`in_dim` selects the number of active spatial dimensions:
- `0`: no active dimensions.
- `1`: x active.
- `2`: x and y active.
- `3`: x, y, and z active.

For each axis, the ghost width is determined independently.

For x:
- if `in_dim >= 1`, base lower ghost width = 1 and base upper ghost width = 1;
- otherwise base lower ghost width = 0 and base upper ghost width = 0.

For y:
- if `in_dim >= 2`, base lower ghost width = 1 and base upper ghost width = 1;
- otherwise base lower ghost width = 0 and base upper ghost width = 0.

For z:
- if `in_dim >= 3`, base lower ghost width = 1 and base upper ghost width = 1;
- otherwise base lower ghost width = 0 and base upper ghost width = 0.

If `in_p_stored_as_transpose == 1`, then along the coarsening direction selected by `in_cdir`, both the lower and upper ghost widths are overridden from 1 to 2. This override applies only when the selected axis is active under `in_dim`; inactive axes keep ghost width 0. `in_cdir = 0` selects x, `1` selects y, `2` selects z. Any other `in_cdir` value causes no override.

The grown fine-grid box is:
- `grown_imin_x = in_fine_imin_x - ghost_lo_x`
- `grown_imax_x = in_fine_imax_x + ghost_hi_x`
- `grown_imin_y = in_fine_imin_y - ghost_lo_y`
- `grown_imax_y = in_fine_imax_y + ghost_hi_y`
- `grown_imin_z = in_fine_imin_z - ghost_lo_z`
- `grown_imax_z = in_fine_imax_z + ghost_hi_z`

The projection retains exactly the lattice coordinates k for which
`gmin <= idx + st*k <= gmax`. For positive stride, the lower endpoint is
`ceil((gmin-idx)/st)` and the upper endpoint is `floor((gmax-idx)/st)`.
For negative stride, swap gmin and gmax in these two expressions. The lower
endpoint may exceed the upper endpoint when no lattice point is present.
This projects onto the coarsening lattice before expressing its bounds in
coarse coordinates; truncating both endpoints toward zero is incorrect.

As explicit task extensions, zero stride or a reversed grown interval returns
the empty interval `(0,-1)` on that axis. Nonpositive stride is not a claim
about the upstream communication library's supported inputs. All arithmetic
is widened before final two's-complement packing modulo 2^32. In particular,
grown-coordinate differences require 34 signed bits at the full input limits.
Geometric interpretations apply to the unwrapped endpoints.

The four output box groups carry identical projected endpoints:
- `out_send_* = projected box`
- `out_recv_* = projected box`
- `out_sendr_* = projected box`
- `out_recvr_* = projected box`

Concretely:
- `out_send_imin_x = proj_min_x`, `out_send_imax_x = proj_max_x`, and similarly for y and z;
- `out_recv_imin_x = proj_min_x`, `out_recv_imax_x = proj_max_x`, and similarly for y and z;
- `out_sendr_imin_x = proj_min_x`, `out_sendr_imax_x = proj_max_x`, and similarly for y and z;
- `out_recvr_imin_x = proj_min_x`, `out_recvr_imax_x = proj_max_x`, and similarly for y and z.

Worked example: with `in_dim = 2`, `in_p_stored_as_transpose = 1`, `in_cdir = 1`, `in_index = (0, 0, 0)`, `in_stride = (2, 3, 1)`, `in_fine_imin = (10, 20, 30)`, and `in_fine_imax = (15, 24, 30)`, the ghost widths are x = (1,1), y = (2,2), z = (0,0); the grown box is `(9,18,30)` to `(16,26,30)`; the projected box is `(5,6,30)` to `(8,8,30)` because `ceil(9/2)=5`, `16/2 -> 8`, `18/3 -> 6`, `26/3 -> 8`, `30/1 -> 30`.

## Reset Behavior

The design uses active-high reset `rst`. Reset may be implemented synchronously or asynchronously. While in reset, there is no retained architectural state. After reset, the kernel is ready to accept an input transaction immediately. No internal state bits require initialization beyond the handshake state needed to realize the required 1:1 transaction behavior. Output payload values during cycles where `out_valid == 0` are don't-care.
