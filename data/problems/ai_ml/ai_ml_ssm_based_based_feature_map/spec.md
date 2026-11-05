# AI ML SSM Based Feature Map

This block computes the scalar feature-map kernel used by the Based sequence mixer for one query/key vector pair. For an input query vector `q` and key vector `k`, it forms the scaled dot product `x = scale * dot(q, k)` and returns the polynomial feature value `phi(x) = 1 + x + 0.5 * x^2`. The interface is a single-clock 1:1 transaction interface: each accepted input transaction produces exactly one output transaction. All arithmetic is signed fixed-point with an explicit Q format and deterministic rounding.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_based_based_feature_map (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_q,
    input  logic [127:0] in_k,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [20:0]  out_s
);
```

Port interpretation:

- `in_q` packs 8 signed Q15 elements, little-endian by lane:
  - lane 0 = `in_q[15:0]`
  - lane 1 = `in_q[31:16]`
  - ...
  - lane 7 = `in_q[127:112]`
- `in_k` is packed identically.
- Each lane is a signed 16-bit Q15 number: value = `raw * 2^-15`.
  - Example: `16'h4000` = `+0.5`
  - Example: `16'hC000` = `-0.5`
- `out_s` is an unsigned 21-bit value with 15 fractional bits (UQ6.15).

The module has no configuration ports. The vector length is fixed at 8. The scale is fixed at 1.0.

### Behavior interface

```python
class AiMlSsmBasedBasedFeatureMap:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_q, in_k):
        pass
```

`process()` arguments:

- `in_q`: 128-bit packed integer carrying 8 signed Q15 lanes.
- `in_k`: 128-bit packed integer carrying 8 signed Q15 lanes.

`process()` returns one dict per accepted input transaction:

```python
{"out_s": <21-bit unsigned integer bit pattern>}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_s`: 21.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_based_based_feature_map`: top-level feature-map datapath and 1-entry output buffer.

## Functional Behavior

For each accepted input transaction, the block shall compute exactly one output `out_s` from the current `in_q` and `in_k`.

Let `q[i]` and `k[i]` for `i = 0..7` be the signed 16-bit integers obtained from lanes `i` of `in_q` and `in_k`, interpreted as Q15.

1. **Dot product in Q30**

   Compute the signed integer dot product

   `dot_q30 = sum_{i=0..7} (q[i] * k[i])`

   Each product `q[i] * k[i]` is a signed Q30 quantity because Q15 × Q15 = Q30. The sum is exact; no wrap, truncation, or saturation is applied before the next step.

2. **Scale**

   The scale is fixed at 1.0, so

   `x_q30 = dot_q30`

   `x_q30` is still Q30.

3. **Polynomial feature map**

   Compute the polynomial

   `phi = 1 + x + 0.5 * x^2`

   in fixed-point as follows:

   - `one_q30 = 1 << 30`
   - `term1_q30 = one_q30 + x_q30`
   - `x2_q60 = x_q30 * x_q30`
   - `half_x2_q30 = x2_q60 >> 31`

   The right shift in the `0.5 * x^2` term converts the non-negative Q60 square term into a Q30 contribution with truncation toward negative infinity, which is identical to truncation toward zero because `x2_q60 >= 0`.

   Then

   `phi_q30 = term1_q30 + half_x2_q30`

   `phi_q30` is the unrounded Q30 result. No saturation is applied at this stage.

4. **Round once to the output format**

`out_s = (phi_q30 + 2**14) >> 15` (nearest, positive ties upward).
For the complete signed-Q15 input domain, `x` lies in [-8,8] and
`1+x+x*x/2` lies in [0.5,41]. Thus 21 unsigned bits with 15 fractional bits
carry every output without clipping. Zero query/key gives 32768 (1.0),
one lane `q=k=0.5` gives 41984 (1.28125), and eight pairs of -1 give
1343488 (41.0). The output error from the stated real polynomial is less
than one output LSB. This is the pairwise polynomial kernel in Based,
not the value reduction, causal mask or normalization of a full attention layer.

## Reset Behavior

Reset is active-high and asynchronous or synchronous implementation is permitted as long as the externally visible reset state matches this section.

Architectural state:

- The module contains a single pending-output state bit indicating whether one output transaction is buffered.
- On reset, the pending-output state bit shall be `0`.

Output ports during and immediately after reset until the first post-reset accepted input:

- `in_ready = 1`
- `out_valid = 0`
- `out_s = 21'd0`

No other architectural state exists.
