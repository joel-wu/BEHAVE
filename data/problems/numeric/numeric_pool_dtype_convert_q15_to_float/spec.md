# Q15ToFloat4

Q15ToFloat4 converts a fixed four-sample vector of signed Q15 values into four IEEE-754 single-precision outputs. Each input sample is interpreted as a two's-complement integer in the range [-32768, 32767] and converted by exact signed scaling with factor 2^-15, i.e. `out[i] = float32(in[i]) / 32768`. The kernel processes one four-element block per accepted input transaction and emits one four-element output transaction.

## Interface

### RTL interface

```systemverilog
module q15_to_float4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class Q15ToFloat4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_to_float4`: top-level kernel. Accepts four Q15 samples and produces four FP32 results for the same block.

## Functional Behavior

Each accepted input transaction contains four independent Q15 samples `in_x0`..`in_x3`. Each `in_xi` is a 16-bit two's-complement signed fixed-point value with 15 fractional bits.

For each lane `i` in `{0,1,2,3}`:

1. Interpret `in_xi` as a signed 16-bit integer:
   - `0x0000` represents integer 0.
   - `0x7FFF` represents integer 32767.
   - `0x8000` represents integer -32768.
   - `0xFFFF` represents integer -1.

2. Convert that signed integer to IEEE-754 single-precision.

3. Scale by `2^-15`, equivalently divide by 32768.

The required mathematical result is:

- `out_y0 = FP32(int16(in_x0) / 32768)`
- `out_y1 = FP32(int16(in_x1) / 32768)`
- `out_y2 = FP32(int16(in_x2) / 32768)`
- `out_y3 = FP32(int16(in_x3) / 32768)`

where `FP32(...)` means the IEEE-754 single-precision encoding of the exact real-number result.

Because every signed 16-bit integer is exactly representable in FP32 and every result is an integer multiple of `2^-15`, each output is exactly representable in FP32. Therefore the required output bit patterns are exactly the IEEE-754 encodings of the real values:

- `out_yi = int16(in_xi) / 32768.0`

The output range is `[-1.0, 0.999969482421875]`, with exact endpoint encodings:
- if `in_xi = 16'h8000`, `out_yi = 32'hBF800000` (`-1.0`)
- if `in_xi = 16'h7FFF`, `out_yi = 32'h3F7FFE00` (`32767 / 32768`)
- if `in_xi = 16'h0000`, `out_yi = 32'h00000000` (`+0.0`)
- if `in_xi = 16'hFFFF`, `out_yi = 32'hB8000000` (`-2^-15`)

The four lanes are independent and have no cross-lane interaction. One accepted input transaction produces exactly one output transaction containing the four converted results.

Edge cases:
- No NaN or infinity can be generated.
- No overflow or underflow can occur.
- Input `16'h0000` produces positive zero `32'h00000000`.
- Negative inputs produce negative finite FP32 outputs according to two's-complement interpretation.
- The most negative Q15 input `16'h8000` maps exactly to `-1.0`.

## Reset Behavior

`rst` is active-high. Reset may be implemented synchronously or asynchronously.

On reset:
- `in_ready` shall be `1`.
- `out_valid` shall be `0`.
- `out_y0` shall be `32'h00000000`.
- `out_y1` shall be `32'h00000000`.
- `out_y2` shall be `32'h00000000`.
- `out_y3` shall be `32'h00000000`.

Architectural state after reset:
- no buffered input block is pending,
- no buffered output block is pending,
- no other architectural state is retained.
