# Chunkwise DeltaNet Delta Rule

This is the one-token recurrence underlying the chunk implementation, with supplied state rather than hidden time state. The legacy task ID does not require a particular chunked microarchitecture. The arithmetic is an integer modular specialization, not a floating-point reproduction.

This block computes one causal DeltaNet delta-rule update step. Each accepted input transaction provides one query vector, one key vector, one value vector, one scalar gate `beta`, and one current state matrix. The block projects the current state along the key, subtracts that projection from the value to form a residual, updates the state by a rank-1 write `beta * outer(key, residual)`, and produces the output by applying the query to the updated state.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_deltanet_deltanet_chunkwise #(
    parameter int KEY_DIM   = 1,
    parameter int VALUE_DIM = 1,
    parameter int ELEM_W    = 16
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [KEY_DIM*ELEM_W-1:0]             in_q,
    input  logic [KEY_DIM*ELEM_W-1:0]             in_k,
    input  logic [VALUE_DIM*ELEM_W-1:0]           in_v,
    input  logic [ELEM_W-1:0]                     in_beta,
    input  logic [KEY_DIM*VALUE_DIM*ELEM_W-1:0]   in_state,

    output logic out_valid,
    input  logic out_ready,
    output logic [VALUE_DIM*ELEM_W-1:0]           out_o,
    output logic [KEY_DIM*VALUE_DIM*ELEM_W-1:0]   out_state
);
```

### Behavior interface

```python
class ChunkwiseDeltaNetDeltaRule:
    def __init__(self, key_dim=1, value_dim=1, elem_w=16): ...
    def reset(self): ...
    def process(self, in_q, in_k, in_v, in_beta, in_state): ...
```

`process(...)` returns one dictionary with keys:
- `out_o`
- `out_state`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_o`: VALUE_DIM*ELEM_W.
- `out_state`: KEY_DIM*VALUE_DIM*ELEM_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor key_dim, value_dim and elem_w match RTL KEY_DIM, VALUE_DIM and ELEM_W.

### Data representation

`in_q`, `in_k`, `in_v`, `out_o`, `in_state`, and `out_state` are integer bit patterns packed in little-endian element order:
- vector element `0` occupies the least-significant `ELEM_W` bits
- for matrices, value index `j` varies fastest, then key index `i`

## Modules

- `ai_ml_ssm_deltanet_deltanet_chunkwise`: top-level block implementing one delta-rule forward step.

## Functional Behavior

The block is stateless across transactions. One accepted input transaction consumes one current state and produces the corresponding updated state and output.

`KEY_DIM >= 1`, `VALUE_DIM >= 1` and `ELEM_W >= 2` are required. All input elements are interpreted as signed `ELEM_W`-bit two's-complement integers. All internal arithmetic uses mathematical signed integers with no intermediate truncation. Final outputs are wrapped modulo `2^ELEM_W` elementwise and returned as unsigned bit patterns of that width.

Let:
- `q[i]` be element `i` of `in_q` for `i in [0, KEY_DIM-1]`
- `k[i]` be element `i` of `in_k`
- `v[j]` be element `j` of `in_v` for `j in [0, VALUE_DIM-1]`
- `beta` be `in_beta`
- `state[i][j]` be the signed element at key index `i`, value index `j` decoded from `in_state`

The computation is:

1. Projection of the current state along the key:
   - for each value index `j`,
     - `proj[j] = sum_{i=0..KEY_DIM-1} k[i] * state[i][j]`

2. Residual value:
   - for each `j`,
     - `residual[j] = v[j] - proj[j]`

3. State update:
   - for each key index `i` and value index `j`,
     - `next_state[i][j] = state[i][j] + beta * k[i] * residual[j]`

4. Output from updated state:
   - for each value index `j`,
     - `out[j] = sum_{i=0..KEY_DIM-1} q[i] * next_state[i][j]`

`out_o` is the packed vector of `out[j]` wrapped to `ELEM_W` bits.
`out_state` is the packed matrix of `next_state[i][j]` wrapped to `ELEM_W` bits.

### Packing and unpacking

For any packed vector `x` of length `N`, element `n` is:
- raw bits: `(x >> (ELEM_W*n)) & ((1 << ELEM_W) - 1)`
- signed value: two's-complement interpretation of those `ELEM_W` bits

For the packed matrix `in_state`, matrix element `(i, j)` is stored at flat element index:
- `flat = i * VALUE_DIM + j`

The packed output matrix `out_state` uses the same layout.

### Edge cases

- Negative query, key, value, beta, and state elements are legal and use signed `ELEM_W`-bit interpretation.
- Zero-sized hardware ports are not supported.
- No scaling factor, l2-normalization, chunk accumulation, variable-length sequence packing, or backward-gradient behavior is part of this block.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst == 1`, the block returns to its idle state.

Architectural state after reset:
- no retained state from prior transactions

Output ports after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_o = 0`
- `out_state = 0`
