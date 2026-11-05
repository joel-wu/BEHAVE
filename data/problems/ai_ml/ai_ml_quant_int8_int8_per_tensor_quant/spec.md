# Eight-Lane Per-Tensor INT8 Affine Quantization

Quantize eight fixed-point values to INT8 using one shared affine quantization scale and zero point.

## Interface

### RTL interface

```systemverilog
module int8_per_tensor_quant (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [127:0] in_values, input logic [31:0] in_scale, input logic [7:0] in_zero_point,
    output logic out_valid, input logic out_ready,
    output logic [63:0] out_quantized, output logic out_error
);
```

### Behavior interface

```python
class Int8PerTensorQuant:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_values, in_scale, in_zero_point): ...
```

Return one mapping with fields `out_quantized`, `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_quantized`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

One input
transfer produces one ordered output transfer. Transfers are sampled on rising
edges with valid and ready high. While output ready is low, a valid result and
all its fields remain unchanged. Arithmetic latency is not fixed.

## Modules

- `int8_per_tensor_quant`: arithmetic and output holding.
- `Int8PerTensorQuant`: the untimed input-to-output mapping.

## Functional Behavior

Each of eight lanes of `in_values` is a signed 16-bit Q8.8 value (integer
bits divided by 256), least-significant lane first. `in_scale` is an unsigned
Q16.16 scale (integer bits divided by 65536), shared across all lanes.
`in_zero_point` is signed eight-bit two's complement.

For nonzero scale, compute for each lane
`q = clamp(round_even(x / scale) + zero_point, -128, 127)`.
The ratio is evaluated exactly for the stated fixed-point inputs; round to
nearest integer with ties to even, then add the zero point, then saturate.
Pack the signed INT8 results into `out_quantized`, lane i at bits 8*i+7:8*i,
and return `out_error=0`. With scale zero return both a zero packed result and
`out_error=1`. The output scale is not estimated from the samples: it is an
input quantization parameter. No stochastic rounding is used.

## Reset Behavior

Reset is synchronous active-high and has priority over transfers. A reset edge
discards pending output, clears output valid and every output field, and makes
input ready. No arithmetic state survives between transactions.
