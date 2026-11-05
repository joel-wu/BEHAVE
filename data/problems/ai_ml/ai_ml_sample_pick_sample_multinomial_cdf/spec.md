# Eight-Way Categorical Inverse CDF

Select one category using an explicit uniform-sample word and a checked finite
CDF. The final endpoint represents probability one exactly.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_pick_sample_multinomial_cdf (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [32:0] in_cdf0,
    input  logic [32:0] in_cdf1,
    input  logic [32:0] in_cdf2,
    input  logic [32:0] in_cdf3,
    input  logic [32:0] in_cdf4,
    input  logic [32:0] in_cdf5,
    input  logic [32:0] in_cdf6,
    input  logic [32:0] in_cdf7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_error,
    output logic [2:0]  out_token_id
);
endmodule
```

### Behavior interface

```python
class AiMlSamplePickSampleMultinomialCdf:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample, in_cdf0, in_cdf1, in_cdf2, in_cdf3, in_cdf4, in_cdf5, in_cdf6, in_cdf7): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_token_id`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The declared top-level module implements the complete operator below.

## Functional Behavior

`in_sample` is unsigned 32-bit U. Each C_i=`in_cdfi` is unsigned
33-bit and denotes a cumulative count out of 2^32. A valid CDF satisfies
0<=C_0<=...<=C_7=2^32. Repeated endpoints encode zero-mass categories.
All physical input words are accepted. On invalid CDF, return out_error=1 and
out_token_id=0. Otherwise return out_error=0 and the smallest i with U<C_i.
This index always exists, including U=0xffffffff. Equality skips an endpoint;
no fallback assigns a missing probability tail to the last category.

The mass of category i is (C_i-C_{i-1})/2^32 with C_{-1}=0. If U is uniform,
these probabilities are exact. Random-number generation and converting logits
to a CDF are outside the operator. The caller may also supply deterministic U
for testing. An invalid input does not affect later requests.

This is an inverse-transform implementation of the categorical draw required by
the source sampler, not vLLM's particular exponential-race RNG algorithm and not
RNG-sequence equivalence. Eight categories and count-valued CDF endpoints are
explicit hardware adaptations; the checked CDF remains the semantic authority.

## Reset Behavior

Reset is synchronous active-high. At a reset edge, discard pending work and
responses and clear output valid and payload. No transaction is accepted while
reset is asserted. Input and output transfer on a rising edge with their valid
and ready both high. A sender holds valid and all payload bits stable until
transfer. Responses are ordered; consume and accept may occur on the same edge.
Each accepted input produces one response, unless a reset interrupts it.
No particular implementation latency is required.
