# DeltaNetDeltaRule

DeltaNetDeltaRule is a single-clock recurrent delta-rule block. Each accepted input transaction performs one state update of a `K x V` matrix state, computes the residual value `u = v - H_old^T k`, scales that residual by `beta`, updates the state with the outer product `k ⊗ (beta * u)`, and computes the output `o = H_new^T (scale * q)`. The block is specified at transaction level: one transaction contains the full vectors and state matrix needed for one recurrent step.

## Interface

### RTL interface

```systemverilog
module DeltaNetDeltaRule #(
    parameter int K = 4,
    parameter int V = 4,
    parameter int W = 32
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic                         in_init,
    input  logic                         in_clear_state,
    input  logic [W-1:0]                 in_scale,
    input  logic [W-1:0]                 in_beta_scalar,
    input  logic                         in_beta_headwise,
    input  logic [K*W-1:0]               in_q,
    input  logic [K*W-1:0]               in_k,
    input  logic [V*W-1:0]               in_v,
    input  logic [V*W-1:0]               in_beta_vector,
    input  logic [K*V*W-1:0]             in_initial_state,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [V*W-1:0]               out_o,
    output logic [V*W-1:0]               out_u,
    output logic [K*V*W-1:0]             out_final_state
);
```

Packing is row-major, least-significant slice first:
- `in_q[(i*W) +: W]` is `q[i]`
- `in_k[(i*W) +: W]` is `k[i]`
- `in_v[(j*W) +: W]` is `v[j]`
- `in_beta_vector[(j*W) +: W]` is `beta_vector[j]`
- `in_initial_state[((i*V + j)*W) +: W]` is `initial_state[i][j]`
- `out_o[(j*W) +: W]` is `o[j]`
- `out_u[(j*W) +: W]` is `u[j]`
- `out_final_state[((i*V + j)*W) +: W]` is `new_state[i][j]`

All `W`-bit elements are signed two's-complement values.

### Behavior interface

```python
class DeltaNetDeltaRule:
    def __init__(self, K=4, V=4, W=32): ...
    def reset(self): ...
    def process(
        self,
        in_init,
        in_clear_state,
        in_scale,
        in_beta_scalar,
        in_beta_headwise,
        in_q,
        in_k,
        in_v,
        in_beta_vector,
        in_initial_state,
    ): ...
```

Behavior transaction representation:
- `in_q`: unsigned `K*W`-bit packed vector using the RTL packing above
- `in_k`: unsigned `K*W`-bit packed vector
- `in_v`: unsigned `V*W`-bit packed vector
- `in_beta_vector`: unsigned `V*W`-bit packed vector
- `in_initial_state`: unsigned `K*V*W`-bit packed matrix

`process(...)` returns one dict:
- `out_o`: unsigned `V*W`-bit packed vector
- `out_u`: unsigned `V*W`-bit packed vector
- `out_final_state`: unsigned `K*V*W`-bit packed matrix

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`: K*V*W.
- `out_o`, `out_u`: V*W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

## Modules

- `DeltaNetDeltaRule`: top-level recurrent delta-rule datapath with one architectural `K x V` state matrix.

## Functional Behavior

The block holds architectural state `H_state[i][j]` for `0 <= i < K`, `0 <= j < V`. Each element is stored as a signed `W`-bit value.

Define:
- `to_signed(x, W)`: truncate `x` to `W` bits, then interpret as signed two's-complement
- `to_unsigned(x, W)`: truncate `x` modulo `2^W`

Input unpacking:
- `q[i] = to_signed((in_q >> (i*W)) & ((1<<W)-1), W)`
- `k[i] = to_signed((in_k >> (i*W)) & ((1<<W)-1), W)`
- `v[j] = to_signed((in_v >> (j*W)) & ((1<<W)-1), W)`
- `beta_vector[j] = to_signed((in_beta_vector >> (j*W)) & ((1<<W)-1), W)`
- `initial_state[i][j] = to_signed((in_initial_state >> ((i*V + j)*W)) & ((1<<W)-1), W)`
- `scale = to_signed(in_scale, W)`
- `beta_scalar = to_signed(in_beta_scalar, W)`

Old-state selection:
1. If `in_clear_state == 1`, `old_state[i][j] = 0` for all `i, j`.
2. Else if `in_init == 1`, `old_state[i][j] = initial_state[i][j]`.
3. Else, `old_state[i][j] = H_state[i][j]`.

If both `in_clear_state` and `in_init` are `1`, clearing takes priority.

Beta selection:
- If `in_beta_headwise == 0`, `beta[j] = beta_scalar` for all `j`
- If `in_beta_headwise == 1`, `beta[j] = beta_vector[j]`

Computation order for one transaction:

1. Projection from old state:
   - `projection_term[i][j] = to_signed(old_state[i][j] * k[i], W)`
   - `projection[j] = to_signed(sum_{i=0..K-1} projection_term[i][j], W)`

2. Residual:
   - `u[j] = to_signed(v[j] - projection[j], W)`

3. Beta-scaled residual:
   - `delta_v[j] = to_signed(u[j] * beta[j], W)`

4. Updated state:
   - `update_term[i][j] = to_signed(k[i] * delta_v[j], W)`
   - `new_state[i][j] = to_signed(old_state[i][j] + update_term[i][j], W)`

5. Scaled query:
   - `scaled_q[i] = to_signed(q[i] * scale, W)`

6. Output from updated state:
   - `output_term[i][j] = to_signed(new_state[i][j] * scaled_q[i], W)`
   - `o[j] = to_signed(sum_{i=0..K-1} output_term[i][j], W)`

After computing `o`, the internal state is replaced by `new_state`.

Returned packed outputs:
- `out_u` packs `to_unsigned(u[j], W)` into slice `(j*W) +: W`
- `out_o` packs `to_unsigned(o[j], W)` into slice `(j*W) +: W`
- `out_final_state` packs `to_unsigned(new_state[i][j], W)` into slice `((i*V + j)*W) +: W`

The block is 1:1: every accepted input transaction produces exactly one output transaction.

## Reset Behavior

`rst` is active-high.

After reset:
- `H_state[i][j] = 0` for all `i, j`

Visible outputs after reset and before the first accepted transaction:
- `out_valid = 0`
- `out_o = 0`
- `out_u = 0`
- `out_final_state = 0`
