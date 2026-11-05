# Paired-Row Symmetric INT8 Quantization

Compute independent max-absolute-value quantizations of two four-element rows.
The rows may be activation and weight data, but both are supplied per request;
this is not a complete linear layer or a persistent static-weight quantizer.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_int8_int8_dynamic_quant (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_act_0,
    input  logic [15:0]  in_act_1,
    input  logic [15:0]  in_act_2,
    input  logic [15:0]  in_act_3,
    input  logic [15:0]  in_weight_0,
    input  logic [15:0]  in_weight_1,
    input  logic [15:0]  in_weight_2,
    input  logic [15:0]  in_weight_3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [16:0]  out_act_scale,
    output logic [16:0]  out_weight_scale,
    output logic [7:0]   out_qact_0,
    output logic [7:0]   out_qact_1,
    output logic [7:0]   out_qact_2,
    output logic [7:0]   out_qact_3,
    output logic [7:0]   out_qweight_0,
    output logic [7:0]   out_qweight_1,
    output logic [7:0]   out_qweight_2,
    output logic [7:0]   out_qweight_3
);
endmodule
```

### Behavior interface

```python
class DynamicInt8Int8Quantizer:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_act_0, in_act_1, in_act_2, in_act_3, in_weight_0, in_weight_1, in_weight_2, in_weight_3): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_act_scale`, `out_weight_scale`: 17.
- `out_qact_0`, `out_qact_1`, `out_qact_2`, `out_qact_3`, `out_qweight_0`,
  `out_qweight_1`, `out_qweight_2`, `out_qweight_3`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The declared top-level module implements the complete operator below.

## Functional Behavior

Every input lane is a signed 16-bit integer. For each row, let
A=max(abs(x_i)), including abs(-32768)=32768. Set S=0 if A=0, otherwise
S=ceil(256*A/127). The output scale is the **complete 17-bit unsigned S**;
its represented real value is S/256 (nine integer and eight fractional bits).
The maximum S is 66053. Never wrap or truncate it before use.

For S=0 return four zero bytes. Otherwise round each 256*x_i/S to nearest
integer, with exact halves away from zero, and clamp to [-127,127]. Return each
signed result as an eight-bit two's-complement word. The scale used for every
element is exactly the scale returned with that row. Both rows are independent.

The rounded quotient can be computed from the nonnegative magnitude as
`q_abs=(256*abs(x_i)+floor(S/2))//S` before restoring the sign. Scale selection
ensures abs(256*x_i/S)<=127. For nonzero rows, dequantization therefore satisfies
`abs(q_i*S/256-x_i)<=S/512`. A zero row is represented exactly.

The source TorchAO configuration motivates per-token activation and per-channel
weight quantization, but delegates their kernels. This task retains symmetric
max-absolute scaling, not TorchAO's complete configuration or backend numerics.
Four integer lanes, upward-rounded fixed-point scales, [-127,127], and ties-away
rounding are declared adaptations. Weight quantization is explicit per request,
not silently baked into the reference.

## Reset Behavior

Reset is synchronous active-high. At a reset edge, discard pending work and
responses and clear output valid and payload. No transaction is accepted while
reset is asserted. Input and output transfer on a rising edge with their valid
and ready both high. A sender holds valid and all payload bits stable until
transfer. Responses are ordered; consume and accept may occur on the same edge.
Each accepted input produces one response, unless a reset interrupts it.
No particular implementation latency is required.
