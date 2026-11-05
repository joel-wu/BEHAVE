# GBDT LGBM Numerical Decision Inner

This block performs one internal numerical tree decision for a LightGBM-style decision tree. For each accepted input transaction it examines a single node's decision metadata, the feature value already quantized into an unsigned bin index, and the special bin codes used to represent missing values. It outputs the next child node index selected by the LightGBM numerical inner-node rule, including explicit handling for the configured missing-value mode and default direction.

## Interface

### RTL interface

```systemverilog
module data_systems_gbdt_lgbm_numerical_decision_inner (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_fval,
    input  logic [31:0] in_threshold_in_bin,
    input  logic [7:0]  in_decision_type,
    input  logic [31:0] in_default_bin,
    input  logic [31:0] in_max_bin,
    input  logic [31:0] in_left_child,
    input  logic [31:0] in_right_child,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_next_node
);
```

### Behavior interface

```python
class GBDTLGBMNumericalDecisionInner:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_fval,
        in_threshold_in_bin,
        in_decision_type,
        in_default_bin,
        in_max_bin,
        in_left_child,
        in_right_child,
    ):
        pass
```

Each call represents one accepted request and returns exactly one dictionary
with key `out_next_node`. Its value is a Python integer in [0, 2**32 - 1],
encoding the selected child's unchanged port word. All input arguments are
unsigned integer encodings: `in_decision_type` is in [0, 255], and the others
are in [0, 2**32 - 1]. Return a dictionary even for child zero, not `None` or a
list. There is no persistent decision state; `reset()` returns no response.

## Modules

- `data_systems_gbdt_lgbm_numerical_decision_inner`: top-level single-stage decision block that implements the LightGBM numerical inner-node branch rule.

## Functional Behavior

All ports are integer bit patterns.

`in_fval`, `in_threshold_in_bin`, `in_default_bin`, and `in_max_bin` are unsigned 32-bit values.

`in_left_child` and `in_right_child` are 32-bit node identifiers returned unchanged on the selected branch. They are treated as opaque 32-bit values; no signed interpretation is applied inside this block.

`in_decision_type` is an 8-bit control field. Only bits `[3:0]` affect behavior:

- bit `0`: categorical-split flag.
- bit `1`: default-left flag.
- bits `[3:2]`: missing-type encoding.

Bits `[7:4]` are ignored.

This block implements only the numerical-inner decision rule. If `in_decision_type[0] == 1`, the node is marked categorical, but this block still applies the same numerical-inner rule using the provided inputs. No categorical bitset lookup is performed.

The missing-type code is:

- `0`: no special missing handling.
- `1`: zero-missing mode.
- `2`: NaN-missing mode.
- `3`: reserved; treated the same as code `3` numerically, which means neither of the special missing comparisons below is true unless explicitly matched by the conditions that use this code value. Since the block only checks for equality with codes `1` and `2`, code `3` behaves the same as code `0`.

The default-left flag is `1` when `in_decision_type[1] == 1`, else `0`.

Let:

- `missing_type = (in_decision_type >> 2) & 2'b11`
- `default_left = ((in_decision_type >> 1) & 1'b1)`

The selected child is computed in this exact order:

1. If `missing_type == 1` and `in_fval == in_default_bin`, then output `in_left_child` when `default_left == 1`, else output `in_right_child`.
2. Else, if `missing_type == 2` and `in_fval == in_max_bin`, then output `in_left_child` when `default_left == 1`, else output `in_right_child`.
3. Else, if `in_fval <= in_threshold_in_bin`, output `in_left_child`.
4. Else, output `in_right_child`.

Comparison in step 3 is an unsigned 32-bit comparison.

`out_next_node` is the selected child value, returned as an unsigned 32-bit port value.

The mapping is 1 input transaction to 1 output transaction.

## Reset Behavior

Reset is synchronous active-high.

The block has no architectural state.

After reset, `out_valid` shall be deasserted until an input transaction is accepted and an output transaction becomes available according to the implementation's latency. `in_ready` and `out_next_node` are unconstrained by reset beyond correct handshake behavior for a stateless 1:1 block.
