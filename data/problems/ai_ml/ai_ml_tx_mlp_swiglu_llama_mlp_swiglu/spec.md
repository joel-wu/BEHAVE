# Four-Channel SwiGLU Gated MLP

Compute a learned 4->8->4 gated MLP: a SiLU gate projection multiplies an
independent up projection elementwise, followed by a down projection. Every
request supplies its input, all three weight matrices and all three biases.
Arithmetic uses signed Q8.8 values with explicit rounding and saturation. There
is no residual connection, normalization, dropout or persistent weight memory.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_mlp_swiglu_llama_mlp_swiglu (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [511:0] in_gate_weight,
    input logic [511:0] in_up_weight,
    input logic [511:0] in_down_weight,
    input logic [127:0] in_gate_bias,
    input logic [127:0] in_up_bias,
    input logic [63:0] in_down_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
```

### Behavior interface

```python
class AiMlTxMlpSwigluLlamaMlpSwiglu:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_gate_weight, in_up_weight, in_down_weight,
                in_gate_bias, in_up_bias, in_down_bias): ...
```

Each call returns one dictionary `{"out_y": packed_output}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

Packed ports are
nonnegative integers; every lane is a signed 16-bit two's-complement integer v
representing the real value v/256. Lane zero occupies bits 15:0.

| Port | Lanes, least-significant first |
| --- | --- |
| `in_x` | x[i], i=0..3 |
| `in_gate_weight`, `in_up_weight` | G[j,i], U[j,i]; lane 4*j+i, j=0..7, i=0..3 |
| `in_down_weight` | D[k,j]; lane 8*k+j, k=0..3, j=0..7 |
| `in_gate_bias`, `in_up_bias` | bg[j], bu[j], j=0..7 |
| `in_down_bias`, `out_y` | bd[k], y[k], k=0..3 |

At rising edges outside reset, an input transfers iff `in_valid && in_ready`
and an output transfers iff `out_valid && out_ready`. Hold an offered request
stable until accepted. Responses are ordered and stable while stalled. Latency
is not fixed. Each accepted request produces exactly one response unless reset
discards it. All input bit patterns are legal.

## Modules

`ai_ml_tx_mlp_swiglu_llama_mlp_swiglu` implements the complete gated MLP and its
request/response interface. Arithmetic sharing and internal scheduling are
implementation choices.

## Functional Behavior

All variables below are decoded **integer lane values**. Let R(v) round a real
number to the nearest integer, with exact halfway cases choosing the even
integer, including negative values. Let S(v)=min(32767,max(-32768,v)). Compute

```text
g[j] = S(R((256*bg[j] + sum_i G[j,i]*x[i]) / 256))
u[j] = S(R((256*bu[j] + sum_i U[j,i]*x[i]) / 256))
h[j] = S(R(A(g[j])*u[j] / 256))
y[k] = S(R((256*bd[k] + sum_j D[k,j]*h[j]) / 256)).
```

Each sum is formed exactly before its single rounding and saturation; do not
round individual products. There is no implicit wraparound. Pack y[k] modulo
65536 into its output lane.

A(g) is the following deterministic SiLU approximation. Define the fixed table
for k=0..128 using the real exponential:

```text
T[k] = R(65536 * (k/16) / (1 + exp(-k/16))).
```

For m=abs(g), set p=m when m>=2048. Otherwise let k=m//16 and r=m%16, and set
`p=R(((16-r)*T[k]+r*T[k+1])/4096)`. Then `A(g)=p` for g>=0 and `A(g)=p-m`
for g<0. This uses SiLU's identity SiLU(-x)=SiLU(x)-x. The table, interpolation,
and tail rule fully determine the output; no runtime transcendental arithmetic
is required. The activation error is less than one Q8.8 unit (1/256 in real
units) over the entire signed input domain. This is an activation bound, not
an error bound for an arbitrarily amplified complete MLP.

Zero weights do not force zero outputs when biases are nonzero. For example,
with zero weights, bg[j]=0, arbitrary bu[j], and bd=[256,-256,0,512], the output
is [256,-256,0,512]. With bg[0]=256, bu[0]=256, D[0,0]=256 and all other lanes
zero, the first output is 187 and the others are zero.

## Reset Behavior

`rst` is synchronous active-high. While asserted, `in_ready=0`. A reset edge
discards unfinished and unconsumed responses and sets `out_valid=0`, `out_y=0`.
After release, the next request uses only its own supplied parameters. The
Computation has no persistent state. Output data is otherwise
unspecified while `out_valid=0`.
