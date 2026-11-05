# SHA256ChMaj

This module computes the two 32-bit Boolean functions used in each SHA-256 compression round: the choice function `CH(x,y,z)` and the majority function `MAJ(x,y,z)`. For each accepted input transaction, it evaluates both functions bitwise on three independent 32-bit operands and returns one output transaction containing both 32-bit results.

## Interface

### RTL interface

```systemverilog
module SHA256ChMaj (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,
    input  logic [31:0] in_y,
    input  logic [31:0] in_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_ch,
    output logic [31:0] out_maj
);
```

### Behavior interface

```python
class SHA256ChMaj:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x, in_y, in_z):
        pass
```

The `process()` return value is a dictionary with keys `out_ch` and `out_maj`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_ch`, `out_maj`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `SHA256ChMaj`: top-level module that accepts one set of 32-bit operands and produces the corresponding SHA-256 `CH` and `MAJ` results.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

All ports `in_x`, `in_y`, `in_z`, `out_ch`, and `out_maj` are 32-bit unsigned bit patterns. The computation is purely bitwise; there is no signed interpretation.

For each accepted input transaction, the module computes:

- `out_ch = ((in_x & in_y) ^ ((~in_x) & in_z)) mod 2^32`
- `out_maj = ((in_x & in_y) ^ (in_x & in_z) ^ (in_y & in_z)) mod 2^32`

`~in_x` is the 32-bit bitwise complement of `in_x`. The complement is truncated to 32 bits before participating in the `out_ch` result, so bit `i` of `~in_x` equals `1 - bit_i(in_x)` for `i = 0..31`.

Equivalently, for each bit position `i` independently:

- `out_ch[i] = in_y[i]` when `in_x[i] = 1`, else `in_z[i]`
- `out_maj[i] = 1` when at least two of `in_x[i]`, `in_y[i]`, and `in_z[i]` are `1`, else `0`

There is exactly one output transaction for each accepted input transaction.

## Reset Behavior

Reset is synchronous active-high on `rst`.

The module has no architectural state.

During reset, `in_ready` shall be `1`, `out_valid` shall be `0`, `out_ch` shall be `0`, and `out_maj` shall be `0`.

After reset is deasserted, the module continues to have no stored state. Any output transaction depends only on the corresponding accepted input transaction.
