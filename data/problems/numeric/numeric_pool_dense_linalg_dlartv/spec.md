# PlaneRotationVectorStep

PlaneRotationVectorStep applies one real plane rotation to one selected element of vector `x` and the corresponding selected element of vector `y`, using one selected cosine/sine pair from coefficient vectors `c` and `s`. The kernel is a single-step inner transaction of a vector rotation sweep: on each accepted input transaction it reads `x[kx]`, `y[ky]`, `c[kc]`, and `s[kc]`, computes the rotated pair `x' = c*x + s*y` and `y' = c*y - s*x`, writes `x'` back to `x[kx]` and `y'` back to `y[ky]`, and returns the two updated values. Arithmetic is signed two's-complement integer arithmetic on 32-bit data and coefficients, with 64-bit intermediate products and sums truncated to 32 bits on writeback and output.

## Interface

### RTL interface

```systemverilog
module plane_rotation_vector_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_x_addr,
    input  logic [7:0]   in_y_addr,
    input  logic [7:0]   in_cs_addr,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x_value,
    output logic [31:0]  out_y_value,

    output logic         x_req_valid,
    output logic         x_req_we,
    output logic [7:0]   x_req_addr,
    output logic [31:0]  x_req_wdata,
    input  logic         x_req_ready,
    input  logic         x_resp_valid,
    input  logic [31:0]  x_resp_rdata,

    output logic         y_req_valid,
    output logic         y_req_we,
    output logic [7:0]   y_req_addr,
    output logic [31:0]  y_req_wdata,
    input  logic         y_req_ready,
    input  logic         y_resp_valid,
    input  logic [31:0]  y_resp_rdata,

    output logic         c_req_valid,
    output logic [7:0]   c_req_addr,
    input  logic         c_req_ready,
    input  logic         c_resp_valid,
    input  logic [31:0]  c_resp_rdata,

    output logic         s_req_valid,
    output logic [7:0]   s_req_addr,
    input  logic         s_req_ready,
    input  logic         s_resp_valid,
    input  logic [31:0]  s_resp_rdata
);
```

### Behavior interface

```python
class PlaneRotationVectorStep:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x_addr, in_y_addr, in_cs_addr):
        pass
```

The `process()` return value is one dict with keys:
- `out_x_value`
- `out_y_value`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x_value`, `out_y_value`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory views self.x, self.y, self.c and self.s each contain 256 unsigned
32-bit words. The environment binds the run images and preserves external memory
across reset. The x/y writes are required side effects, not replaced by the returned
values; request pins are not return fields.

## Modules

- `plane_rotation_vector_step`: top-level module implementing one plane-rotation update transaction with external memories `x`, `y`, `c`, and `s`.

## Functional Behavior

Each accepted input transaction supplies three 8-bit addresses:
- `in_x_addr`: address of the `x` element to update.
- `in_y_addr`: address of the `y` element to update.
- `in_cs_addr`: address of the cosine/sine coefficient pair to use.

The external memories have depth 256 words each. Each memory word is 32 bits.

Interpretation of memory contents:
- `x[in_x_addr]` and `y[in_y_addr]` are signed 32-bit two's-complement integers.
- `c[in_cs_addr]` and `s[in_cs_addr]` are signed 32-bit two's-complement integers.

For one transaction, let:
- `x_old = signed32(x[in_x_addr])`
- `y_old = signed32(y[in_y_addr])`
- `c_val = signed32(c[in_cs_addr])`
- `s_val = signed32(s[in_cs_addr])`

The kernel computes exactly these expressions, in this order, from the pre-write values:
- `x_rot_full = c_val * x_old + s_val * y_old`
- `y_rot_full = c_val * y_old - s_val * x_old`

Both multiplications and the following add/subtract are mathematically performed on signed integers wide enough to avoid overflow before truncation. The required observable result is equivalent to signed 64-bit evaluation of both products and the final sum or difference.

Writeback values are the low 32 bits of the full results:
- `x_new_bits = x_rot_full mod 2^32`
- `y_new_bits = y_rot_full mod 2^32`

These 32-bit patterns are written back to memory:
- `x[in_x_addr] := x_new_bits`
- `y[in_y_addr] := y_new_bits`

The output transaction returns the same two 32-bit writeback patterns:
- `out_x_value = x_new_bits`
- `out_y_value = y_new_bits`

All truncation is wraparound truncation to 32 bits. There is no saturation, rounding, normalization, or validity checking on the coefficient pair.

Read/write ordering requirement:
- The values used in both formulas are the old memory contents read before either write occurs.
- If `in_x_addr` equals `in_y_addr`, the reads still come from different memories `x` and `y`; there is no aliasing between the two memories.
- The coefficient memories `c` and `s` are read-only from the kernel point of view.

Memory-port ordering requirement:
- The transaction performs one read from `x`, one read from `y`, one read from `c`, one read from `s`, then one write to `x` and one write to `y`.
- If an implementation issues requests on different ports in different cycles due to stalls, the final committed behavior must still match the single-transaction computation above.
- Because writes target `x` and `y` only after all reads for the transaction are determined, the result is equivalent to a read-before-write update.

Edge cases:
- Every 32-bit input bit pattern is legal.
- Negative operands are interpreted by two's-complement signed conversion.
- Overflow of either product or either final add/subtract does not raise an exception; only the low 32 bits are kept.
- Zero coefficients or zero data values follow the same formulas with no special cases.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

Architectural state after reset:
- No transaction is in flight.
- No buffered output is pending.

Output/interface state after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_x_value = 32'h00000000`
- `out_y_value = 32'h00000000`
- `x_req_valid = 0`
- `x_req_we = 0`
- `x_req_addr = 8'h00`
- `x_req_wdata = 32'h00000000`
- `y_req_valid = 0`
- `y_req_we = 0`
- `y_req_addr = 8'h00`
- `y_req_wdata = 32'h00000000`
- `c_req_valid = 0`
- `c_req_addr = 8'h00`
- `s_req_valid = 0`
- `s_req_addr = 8'h00`
