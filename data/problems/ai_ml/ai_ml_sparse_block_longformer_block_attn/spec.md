# Longformer Local/Global Attention Core

Compute local/global attention for eight tokens with one four-dimensional head.
Inputs are already projected ordinary and global Q/K/V vectors. Ordinary queries
attend to their radius-two window plus global tokens; global queries use their
separate projections to attend to every unmasked token. Each request returns the
complete normalized weighted-value sequence. Projection layers, output projection,
residuals, normalization layers and dropout are outside this core.

## Interface

### RTL interface

```systemverilog
module LongformerBlockAttention (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_query,
    input logic [511:0] in_key,
    input logic [511:0] in_value,
    input logic [511:0] in_global_query,
    input logic [511:0] in_global_key,
    input logic [511:0] in_global_value,
    input logic [7:0] in_masked,
    input logic [7:0] in_global,
    output logic out_valid,
    input logic out_ready,
    output logic [511:0] out_attn_out
);
```

### Behavior interface

```python
class LongformerBlockAttention:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_query, in_key, in_value, in_global_query,
                in_global_key, in_global_value, in_masked, in_global): ...
```

The Behavior returns `{"out_attn_out": packed_output}` with a nonnegative packed
integer.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_out`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Every vector port contains 32 signed Q8.8 lanes, each encoded in 16-bit two's
complement. Lane `4*t+c` occupies bits `[16*(4*t+c)+15:16*(4*t+c)]`, for token
t=0..7 and channel c=0..3. Output uses the same order and representation.

### Mask semantics

Bit t of `in_masked` disables token t; bit t of `in_global` marks it
global. If both bits are set, masking takes priority. All port bit patterns
are legal.

### Transaction protocol

A request transfers on a rising edge outside reset when `in_valid && in_ready`;
a response transfers when `out_valid && out_ready`. Hold offered input values
while awaiting acceptance and hold a valid output while stalled. Requests and
responses are ordered, one whole response per accepted request unless reset
discards it. Latency and internal scheduling are not prescribed.

## Modules

`LongformerBlockAttention` implements the complete sequence core and transport.
No state or projection coefficients are shared between requests.

## Functional Behavior

Decode Q8.8 lanes to their signed integer words. All formulas below use those
words and mathematical integers. For query token i:

1. If i is masked, its four output lanes are zero.
2. Otherwise form the set A of unmasked target positions j. If i is global,
   include every such j and use global Q[i], K[j] and V[j]. If i is ordinary,
   include j when it is global or `i-2 <= j <= i+2`, and use ordinary Q/K/V
   for **all** included targets, including global targets. A position satisfying
   both tests is included once. Window arithmetic does not wrap at the ends.
3. For each j in A, compute
   `s[j] = floor(sum_c Q[i,c]*K[j,c]/512)` and `p=max(s[j] for j in A)`.
   The factor 512 combines Q8.8 rescaling and head scale 1/sqrt(4).
4. Set `g[j]=floor((p-s[j]+8)/16)`. Its weight is E[g[j]] from the table
   below when g[j]<=128, and zero otherwise. Excluded positions have zero
   weight. The table approximates the exponential of the relative score;
   it is not a Boolean top-k mask.
5. Return each channel as
   `floor(sum_j weight[j]*V[j,c] / sum_j weight[j])`.
   A is nonempty for every unmasked query because it includes itself, and a
   maximum-score position has weight E[0]=65536. Thus the denominator is positive.
   This weighted average fits signed 16 bits. Pack each lane modulo 2^16.

All floors round toward negative infinity; no intermediate overflow, hidden
rounding or saturation is allowed. The exact E[0..128] words are
`floor(65536*exp(-g/16)+0.5)`:

```text
65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954
30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566
14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353
6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473
3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641
1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775
728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366
344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173
162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82
77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39
36, 34, 32, 30, 28, 27, 25, 23, 22
```

For example, set all Q and K lanes (ordinary and global) to zero. Let every
ordinary V lane of token j equal 256*j. With no masked/global tokens, query 0
averages tokens 0,1,2 and returns 256 in each lane. Mark token 7 global:
ordinary query 0 now averages 0,1,2,7 and returns 640. If every global V lane
equals 512, global query 7 returns 512 in every lane. The latter uses the
global projections, not the ordinary values used by query 0.

## Reset Behavior

Reset is synchronous active-high. While `rst` is asserted `in_ready=0`; at
a reset edge discard all pending work, clear `out_valid` and set the output
word to zero. The next accepted request is independent of prior work.
Output data is otherwise unspecified while
`out_valid=0`.
