# Finite-Precision Nucleus Filter

Return an eight-token top-p keep mask using an ascending cumulative-probability
cut and at least one survivor. Probabilities use the exact finite table below;
this is not a bit-exact floating-point softmax.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_filter_sample_topp_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [16:0]  in_top_p,
    input  logic [127:0] in_logits,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_mask
);
endmodule
```

### Behavior interface

```python
class AiMlSampleFilterSampleToppMask:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_top_p, in_logits): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_mask`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The declared top-level module implements the complete operator below.

## Functional Behavior

Token i occupies bits `[16*i +: 16]` of `in_logits` and is a signed
16-bit integer logit. The 17-bit probability control represents
`p=min(in_top_p,65536)/65536`; in particular 65536 encodes one exactly.
All input words are legal.

Let d_i be the maximum logit minus logit i, computed without signed overflow.
For d=0..22 use `round(exp(-d)*2^32)` (nearest, ties to even):

```
4294967296, 1580030169, 581260615, 213833830, 78665070, 28939262, 10646160, 3916503, 1440801, 530041, 194991, 71733, 26389, 9708, 3571, 1314, 483, 178, 65, 24, 9, 3, 1
```

For d>=23 use zero. The d=0 weight is exactly 4294967296, requiring 33 bits.
Sum all weights to obtain D, which is always positive. Order tokens by ascending
signed logit, breaking ties by increasing index. Let C_i include the weights
through token i in that order. Retain token i if `C_i*65536 > (65536-p_word)*D`,
where `p_word=min(in_top_p,65536)`. Also retain the final token in the order
unconditionally. Set `out_mask[i]` for retained tokens. Sums and products are
exact integers; there is no normalized-probability rounding.

Uniform logits and p=1/2 yield mask 0xF0; p=0 keeps only the highest-index maximum.
p=1 keeps all positive-weight tokens; table-zero tails are removed. This is the
finite table's zero probability, not a claim that real exponential tails vanish.

The archived vLLM sampler delegates this operation. Its PyTorch dependency and
the archived Transformers `TopPLogitsWarper` use the ascending cut and minimum-one
rule. Fixed dimensions, integer logits, this table and deterministic tie order
are explicit adaptations; no random draw or GPU backend is implemented here.

## Reset Behavior

Reset is synchronous active-high. At a reset edge, discard pending work and
responses and clear output valid and payload. No transaction is accepted while
reset is asserted. Input and output transfer on a rising edge with their valid
and ready both high. A sender holds valid and all payload bits stable until
transfer. Responses are ordered; consume and accept may occur on the same edge.
Each accepted input produces one response, unless a reset interrupts it.
No particular implementation latency is required.
