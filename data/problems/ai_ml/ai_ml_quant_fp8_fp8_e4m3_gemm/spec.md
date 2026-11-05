# AiMlQuantFp8Fp8E4m3Gemm

A 2x2 E4M3FN matrix multiply with operand transposition and alpha/beta
accumulation. Products and sums are exact; each final element is rounded once
to E4M3FN. This specifies a numerical operator, not GPU accumulation order.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_fp8_fp8_e4m3_gemm (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_layout,
    input  logic [31:0]  in_alpha,
    input  logic [31:0]  in_beta,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_c0,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_d0
);
```

Each matrix word packs row-major bytes: (0,0) at bits7:0, (0,1) at15:8,
(1,0) at23:16, and (1,1) at31:24. Alpha and beta are unsigned 32-bit integers.
Layout1 means NN, layout2 means NT, and layouts0/3 mean TN; T transposes that
operand. Every input transfer produces one ordered output. Transfers occur
at rising edges when valid and ready are high; pending outputs remain stable
under backpressure.

### Behavior interface

```python
class AiMlQuantFp8Fp8E4m3Gemm:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_layout, in_alpha, in_beta, in_a0, in_b0, in_c0): ...
```

The result is a record with packed field `out_d0`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_d0`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_quant_fp8_fp8_e4m3_gemm`: matrix arithmetic and output quantization.

## Functional Behavior

An E4M3FN byte has sign s, four-bit exponent e, and three-bit fraction f.
For e=0 its value is (-1)^s*f*2^-9. Otherwise its value is
(-1)^s*(1+f/8)*2^(e-7), except e=15,f=7 denotes NaN. There are no infinities;
the maximum finite magnitude is448. Both signed zeros participate as zero.

After the selected transpositions, each output element is
alpha*(A[r,0]*B[0,c]+A[r,1]*B[1,c])+beta*C[r,c]. Intermediate arithmetic is
exact, including subnormals. No input or intermediate scale is implicit.

Any NaN among that element's four multiplicands or its C element produces
canonical byte0x7F, even if the corresponding alpha or beta is zero. Otherwise
choose the nearest finite E4M3FN value, with midpoint ties resolved to the
even low fraction bit. Values beyond +/-448 saturate to bytes0x7E/0xFE.
An exact zero encodes as +0; a negative nonzero value rounded to zero encodes
as -0. Pack the four results in the same row-major byte order.

For alpha=1,beta=0, identity A and diagonal B=(1,2) produce the same diagonal
matrix. For alpha=0,beta=1 and finite A/B, the output reproduces C except
that exact signed zeros normalize to +0. The two NaN encodings are never
treated as finite +/-480.

## Reset Behavior

Asynchronous active-high reset cancels pending output and clears out_valid
and out_d0. There is no persistent numerical state. No input is consumed
while reset is active.
