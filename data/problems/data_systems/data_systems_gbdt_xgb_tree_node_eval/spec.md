# XGBoost Numeric/Categorical Tree-Node Step

Select a child for one already-fetched tree node. Numeric nodes compare a
binary32 feature with their threshold. Categorical
nodes use the source's category **bitset**: a set bit selects the right child,
not the left. An enabled missing-value flag selects the default child before
any split is inspected. This primitive does not traverse or train a whole tree.

## Interface

### RTL interface

```systemverilog
module XGBoostTreeNodeEvaluator (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_fvalue,
    input logic in_is_missing,
    input logic in_has_missing,
    input logic in_has_categorical,
    input logic in_node_is_categorical,
    input logic [31:0] in_split_cond,
    input logic [31:0] in_left_child,
    input logic [31:0] in_default_child,
    input logic [3:0] in_cat_begin,
    input logic [3:0] in_cat_size,
    input logic [255:0] in_cat_words,
    output logic out_valid,
    input logic out_ready,
    output logic out_decision,
    output logic [31:0] out_next_node,
    output logic out_error
);
```

### Behavior interface

```python
class XGBoostTreeNodeEvaluator:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_fvalue, in_is_missing, in_has_missing, in_has_categorical,
                in_node_is_categorical, in_split_cond, in_left_child, in_default_child,
                in_cat_begin, in_cat_size, in_cat_words): ...
```

Each call represents one accepted request and returns exactly one dictionary
with keys `out_decision`, `out_next_node`, and `out_error`. The decision and
error flags are Python integers 0 or 1; the node is an integer in
[0, 2**32 - 1]. Return all fields on default and error routes too, not `None`
or a list. Input arguments are unsigned integer encodings at their declared
port widths. There is no persistent node state; `reset()` returns no response.

## Modules

`XGBoostTreeNodeEvaluator` computes one traversal decision. The independent
per-prediction GetTreeLimit helper is outside this node-step interface.
Node metadata are request inputs; no category or tree memory is hidden in the DUT.

## Functional Behavior

All integer metadata are unsigned.
`in_fvalue` and `in_split_cond` are raw binary32 words, including subnormals,
signed zero, infinities and NaNs; there is no DAZ/FTZ conversion.

`in_cat_words` packs eight uint32 words: word w occupies bits 32*w +: 32.
`in_cat_begin` and `in_cat_size` select a contiguous span in units of words,
not individual categories. The span is valid exactly when begin+size<=8
(using a nonwrapping sum). For category k, its bit is **31-(k mod 32)** in
span word floor(k/32), matching XGBoost's left-bit-first `LBitField32`.
Thus category zero is the most significant bit of the first selected word.
Each span can describe up to 256 categories.

One request transfers on a rising edge outside reset when `in_valid && in_ready`;
one response transfers when `out_valid && out_ready`. Keep offered inputs stable
while waiting, and keep valid outputs stable while stalled. Responses are ordered,
one per accepted request unless reset discards it. Latency is not prescribed.

1. If `in_has_missing && in_is_missing`, return the default child,
   `out_decision=0` and `out_error=0`. All split/category fields are ignored.
2. Otherwise, categorical mode is enabled only when both
   `in_has_categorical` and `in_node_is_categorical` are one.
   - In numeric mode, go left iff feature<threshold in binary32 ordering.
     Either NaN makes the comparison false; +0 and -0 compare equal.
     Category metadata are ignored, even if their span is invalid.
   - In categorical mode, an invalid span or NaN feature returns
     `out_error=1`, `out_decision=0`, `out_next_node=0`. This defines otherwise
     invalid memory/conversion cases.
   - For other categorical values, negative values or values >=16777216
     go left. This includes both infinities. Negative zero is zero, not negative.
   - Otherwise convert the nonnegative finite feature to integer k by truncating
     its fractional part. If k>=32*size, go left. If within the span, go left
     iff its selected bit is zero. For example, 3.75 addresses category 3.
3. Outside an error/default route, `out_decision=1` denotes the left child;
   `out_next_node` is `in_left_child` when left, or `in_left_child+1` modulo
   2^32 when right. `out_error=0`.

The fractional-category conversion follows the source C++ casts; typical model
inputs use integer categories. The explicit categorical-NaN error avoids an
undefined float-to-integer conversion; NaN can instead be marked missing.
No category list search or cap-to-eight-categories behavior is implied.

Example: let begin=1, size=1, word 1=0x10000000 and all other words zero.
This sets category 3. In categorical mode, feature 3.0 or 3.75 goes right;
feature 4.0 goes left. Changing begin to zero changes 3.0 to a left decision.
An enabled missing flag selects the default child regardless of these values.

## Reset Behavior

Reset is synchronous active-high. While reset is asserted `in_ready=0`; a reset
edge discards pending responses, clears `out_valid` and zeroes every output.
Output data is otherwise unspecified while
`out_valid=0`.
