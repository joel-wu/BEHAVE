# PQLUTScan

Compute one asymmetric PQ distance from four selected binary32 lookup values, accumulating subquantizers in order.

## Interface

### RTL interface

```systemverilog
module data_systems_vector_search_pq_search_lut_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_lut_0,
    input  logic [31:0]  in_lut_1,
    input  logic [31:0]  in_lut_2,
    input  logic [31:0]  in_lut_3,
    input  logic [31:0]  in_lut_4,
    input  logic [31:0]  in_lut_5,
    input  logic [31:0]  in_lut_6,
    input  logic [31:0]  in_lut_7,
    input  logic [31:0]  in_lut_8,
    input  logic [31:0]  in_lut_9,
    input  logic [31:0]  in_lut_10,
    input  logic [31:0]  in_lut_11,
    input  logic [31:0]  in_lut_12,
    input  logic [31:0]  in_lut_13,
    input  logic [31:0]  in_lut_14,
    input  logic [31:0]  in_lut_15,
    input  logic [31:0]  in_code_0,
    input  logic [31:0]  in_code_1,
    input  logic [31:0]  in_code_2,
    input  logic [31:0]  in_code_3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_distance
);
```

### Behavior interface

```python
class PQLUTScan:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_lut_0,
        in_lut_1,
        in_lut_2,
        in_lut_3,
        in_lut_4,
        in_lut_5,
        in_lut_6,
        in_lut_7,
        in_lut_8,
        in_lut_9,
        in_lut_10,
        in_lut_11,
        in_lut_12,
        in_lut_13,
        in_lut_14,
        in_lut_15,
        in_code_0,
        in_code_1,
        in_code_2,
        in_code_3,
    ): ...
```

Each call represents one accepted input and returns exactly
`{"out_distance": value}`, with `value` an unsigned 32-bit FP32 encoding.
All arguments are unsigned 32-bit integers; LUT entries are raw FP32 encodings,
not Python floats. The return is never `None` or a list. Requests are
independent; `reset()` returns `None` without emitting a record.

## Modules

- `data_systems_vector_search_pq_search_lut_scan`: the complete operator and its input/output handshake.
- `fp32_add`: combinational binary32 addition using the specified numeric profile.

## Functional Behavior

There are four subquantizers with four lookup entries each. For m in 0..3, c[m] is the low two bits of in_code_m; higher bits are ignored. Select v[m] = in_lut_(4*m+c[m]). Starting at positive zero, perform four additions in order:

    d0 = +0; d1 = add(d0,v[0]); d2 = add(d1,v[1]);
    d3 = add(d2,v[2]); out_distance = add(d3,v[3]).

Each addition rounds separately using the binary32 profile below. Reassociation into a balanced sum is not equivalent. For selected values (16777216,1,1,1), the result is 16777216, not 16777218. No heap selection or top-k search is performed.

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Asynchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
