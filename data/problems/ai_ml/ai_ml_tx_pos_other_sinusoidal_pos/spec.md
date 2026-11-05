# AiMlTxPosOtherSinusoidalPos

This block generates one Whisper-style sinusoidal positional embedding vector for a requested token position and channel count. The computation matches the `sinusoids(length, channels, max_timescale=10000)` construction evaluated at a single position index: for each channel pair, a geometric inverse-timescale is formed, the position is multiplied by that inverse-timescale, and the outputs are the sine half followed by the cosine half. The implementation contract is restricted to channel counts 2, 4, 6, 8, 10, 12, 14, and 16, producing one 16-lane FP32 output transaction per accepted input transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_pos_other_sinusoidal_pos (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_position,
    input  logic [31:0]  in_channels,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_e0,
    output logic [31:0]  out_e1,
    output logic [31:0]  out_e2,
    output logic [31:0]  out_e3,
    output logic [31:0]  out_e4,
    output logic [31:0]  out_e5,
    output logic [31:0]  out_e6,
    output logic [31:0]  out_e7,
    output logic [31:0]  out_e8,
    output logic [31:0]  out_e9,
    output logic [31:0]  out_e10,
    output logic [31:0]  out_e11,
    output logic [31:0]  out_e12,
    output logic [31:0]  out_e13,
    output logic [31:0]  out_e14,
    output logic [31:0]  out_e15
);
```

### Behavior interface

```python
class AiMlTxPosOtherSinusoidalPos:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_position, in_channels): ...
```

The `process()` method returns one dict per accepted input transaction with keys:
`out_e0`, `out_e1`, `out_e2`, `out_e3`, `out_e4`, `out_e5`, `out_e6`, `out_e7`,
`out_e8`, `out_e9`, `out_e10`, `out_e11`, `out_e12`, `out_e13`, `out_e14`, `out_e15`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_e0`, `out_e1`, `out_e10`, `out_e11`, `out_e12`, `out_e13`, `out_e14`,
  `out_e15`, `out_e2`, `out_e3`, `out_e4`, `out_e5`, `out_e6`, `out_e7`, `out_e8`,
  `out_e9`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_pos_other_sinusoidal_pos`: top-level sinusoidal positional embedding generator.

## Functional Behavior

One accepted input transaction produces exactly one output transaction.

`in_position` is an unsigned 32-bit integer position index `p`.

`in_channels` is an unsigned 32-bit integer channel count `c`. Legal values are exactly `2, 4, 6, 8, 10, 12, 14, 16`. Behavior for any other `c` is defined and returns all-zero outputs.

All `out_e*` ports carry raw IEEE-754 FP32 bit patterns as unsigned 32-bit integers.

The block computes a 16-element output vector `e[0..15]`. Elements with index `i >= c` are zero. Elements with index `i < c` are computed from the Whisper sinusoidal embedding formula with `max_timescale = 10000.0`.

For legal `c`, define `half = c / 2`.

For each pair index `j` in `[0, half-1]`, define the real-valued inverse timescale:
- if `half == 1`, `inv_timescale(j) = 1.0`
- otherwise `inv_timescale(j) = exp(-log(10000.0) * j / (half - 1))`

Then define:
- `angle(j) = p * inv_timescale(j)`
- `e[j] = sin(angle(j))`
- `e[half + j] = cos(angle(j))`

Output ordering is therefore all sine channels first, then all cosine channels, matching:
`cat([scaled_time.sin(), scaled_time.cos()], dim=1)` for a single position row.

For all `k` in `[c, 15]`, `e[k] = 0.0`.

Each `e[k]` is converted to FP32 and emitted as the exact IEEE-754 single-precision bit pattern for that real value. `+0.0` is encoded as `0x00000000`.

The computed domain is `0 <= in_position <= 15` and the listed even channel counts. Other positions or channel counts produce one all-zero record. Evaluate the sine/cosine formulas as real numbers, then round each final value to binary32, nearest ties to even; do not round the intermediate frequency or angle to binary32. A worked example:
- `in_position = 1`, `in_channels = 4`
- `half = 2`
- `inv_timescale(0) = 1.0`
- `inv_timescale(1) = 0.0001`
- `e[0] = sin(1.0)`
- `e[1] = sin(0.0001)`
- `e[2] = cos(1.0)`
- `e[3] = cos(0.0001)`
- `e[4]..e[15] = 0.0`

This yields:
- `out_e0 = 0x3F576AA4`
- `out_e1 = 0x38D1B717`
- `out_e2 = 0x3F0A5140`
- `out_e3 = 0x3F800000`
- `out_e4..out_e15 = 0x00000000`

Another example:
- `in_position = 0`, any legal `in_channels = c`
- every sine term is `sin(0) = 0.0`
- every cosine term is `cos(0) = 1.0`
- for `c = 6`, outputs are `e[0]=0`, `e[1]=0`, `e[2]=0`, `e[3]=1`, `e[4]=1`, `e[5]=1`, and `e[6]..e[15]=0`

NaN, infinity, and subnormal handling does not arise for legal inputs `in_position in [0,15]` and legal `in_channels`. For illegal `in_channels`, all outputs are `+0.0`.

## Reset Behavior

Reset is asynchronous active-high. On any cycle with `rst = 1`, the block returns to its idle state.

Architectural state after reset:
- no input transaction is buffered
- no output transaction is pending

Output port values after reset:
- `out_valid = 0`
- `out_e0 = 32'h00000000`
- `out_e1 = 32'h00000000`
- `out_e2 = 32'h00000000`
- `out_e3 = 32'h00000000`
- `out_e4 = 32'h00000000`
- `out_e5 = 32'h00000000`
- `out_e6 = 32'h00000000`
- `out_e7 = 32'h00000000`
- `out_e8 = 32'h00000000`
- `out_e9 = 32'h00000000`
- `out_e10 = 32'h00000000`
- `out_e11 = 32'h00000000`
- `out_e12 = 32'h00000000`
- `out_e13 = 32'h00000000`
- `out_e14 = 32'h00000000`
- `out_e15 = 32'h00000000`

`in_ready` is high whenever no output transaction is pending and may also be high in the same cycle an existing output transaction is accepted.
