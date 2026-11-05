# GPTNeoX MLP GELU

This block implements the GPT-NeoX feed-forward sublayer for one hidden-state vector. It performs a first affine projection from hidden size to intermediate size, applies the GPT-NeoX GELU activation, then performs a second affine projection back to hidden size. The block is stateless across transactions. All datapath values are signed fixed-point integers in one common Q format, and the GELU nonlinearity uses a fixed rational approximation of `tanh` so the result is fully specified without native floating-point arithmetic.

Supported configurations have `1 <= HIDDEN_SIZE, INTERMEDIATE_SIZE <= 16`, `8 <= DATA_W <= 24`, and `1 <= FRAC_W <= DATA_W-2`. The default is 4 by 4, signed Q12. This is a fixed-point adaptation of the two-affine GELU sublayer, not bit equivalence to its floating-point implementation.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_mlp_geglu_gpt_neox_mlp_gelu #(
    parameter int HIDDEN_SIZE = 4,
    parameter int INTERMEDIATE_SIZE = 4,
    parameter int DATA_W = 16,
    parameter int FRAC_W = 12
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [HIDDEN_SIZE*DATA_W-1:0] in_hidden_states,
    input  logic signed [HIDDEN_SIZE*INTERMEDIATE_SIZE*DATA_W-1:0] in_w1,
    input  logic signed [INTERMEDIATE_SIZE*DATA_W-1:0] in_b1,
    input  logic signed [HIDDEN_SIZE*INTERMEDIATE_SIZE*DATA_W-1:0] in_w2,
    input  logic signed [HIDDEN_SIZE*DATA_W-1:0] in_b2,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [HIDDEN_SIZE*DATA_W-1:0] out_hidden_states
);
```

Packing rules:
- `in_hidden_states` and `out_hidden_states` are vectors of `HIDDEN_SIZE` signed `DATA_W`-bit elements.
- `in_b1` is a vector of `INTERMEDIATE_SIZE` signed `DATA_W`-bit elements.
- `in_b2` is a vector of `HIDDEN_SIZE` signed `DATA_W`-bit elements.
- For every packed vector, element 0 occupies bits `[DATA_W-1:0]`, element 1 occupies bits `[2*DATA_W-1:DATA_W]`, and so on.
- `in_w1` is a row-major matrix with `INTERMEDIATE_SIZE` rows and `HIDDEN_SIZE` columns. Element `w1[i][j]` occupies packed element index `i*HIDDEN_SIZE + j`.
- `in_w2` is a row-major matrix with `HIDDEN_SIZE` rows and `INTERMEDIATE_SIZE` columns. Element `w2[k][i]` occupies packed element index `k*INTERMEDIATE_SIZE + i`.

Numeric format:
- Every scalar port element is a signed two's-complement integer of width `DATA_W`.
- Every scalar represents a Q`FRAC_W` value with real value `raw * 2^-FRAC_W`.
- With the default parameters, `0x1000 = +1.0`, `0x0800 = +0.5`, `0xF000 = -1.0`.

### Behavior interface

```python
class GPTNeoXMLPGELU:
    def __init__(self, hidden_size=4, intermediate_size=4, data_w=16, frac_w=12): ...
    def reset(self): ...
    def process(self, in_hidden_states, in_w1, in_b1, in_w2, in_b2): ...
```

`process(...)` returns:
- `{"out_hidden_states": <packed DATA_W*HIDDEN_SIZE-bit integer>}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_hidden_states`: HIDDEN_SIZE*DATA_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings match the uppercase RTL parameters.

## Modules

- `ai_ml_tx_mlp_geglu_gpt_neox_mlp_gelu`: top-level fixed-point GPT-NeoX MLP block.

## Functional Behavior

Let:
- `Q = FRAC_W`
- `MIN = -(1 << (DATA_W - 1))`
- `MAX = (1 << (DATA_W - 1)) - 1`
- `ONE = 1 << Q`

Define these helper operations:

1. `sat_data(v)`:
   - if `v < MIN`, return `MIN`
   - else if `v > MAX`, return `MAX`
   - else return `v`

2. `qmul(a, b)`:
   - Multiply signed integers with full precision: `prod = a * b`
   - Return `prod >> Q`
   - `>>` is arithmetic right shift on a signed integer
   - This is truncation toward negative infinity

3. `div_trunc_zero(n, d)`:
   - This helper is used only inside the tanh approximation
   - if `d == 0`, return `0`
   - otherwise divide the signed integers with truncation toward zero:
     - if `n >= 0`, return `n // d`
     - else return `-(( -n) // d)`

For one accepted input transaction, the block computes the following steps in order.

### 1. Unpack inputs

Unpack:
- `x[j]` from `in_hidden_states`, `j = 0 .. HIDDEN_SIZE-1`
- `b1[i]` from `in_b1`, `i = 0 .. INTERMEDIATE_SIZE-1`
- `b2[k]` from `in_b2`, `k = 0 .. HIDDEN_SIZE-1`
- `w1[i][j]` from `in_w1`, `i = 0 .. INTERMEDIATE_SIZE-1`, `j = 0 .. HIDDEN_SIZE-1`
- `w2[k][i]` from `in_w2`, `k = 0 .. HIDDEN_SIZE-1`, `i = 0 .. INTERMEDIATE_SIZE-1`

Every unpacked scalar is sign-extended from `DATA_W` bits and interpreted as Q`Q`.

### 2. First affine projection

For each intermediate lane `i`:

- Start with `acc1 = b1[i]`
- For `j = 0 .. HIDDEN_SIZE-1`, update:
  - `acc1 = acc1 + qmul(w1[i][j], x[j])`
- After all terms are accumulated, compute:
  - `pre_gelu[i] = sat_data(acc1)`

No saturation occurs inside the sum before the final `sat_data(acc1)`.

### 3. GELU activation

The activation is applied independently to each `pre_gelu[i]`.

For one input `xg = pre_gelu[i]`:

1. Compute powers in Q`Q`:
   - `x2 = qmul(xg, xg)`
   - `x3 = qmul(x2, xg)`

2. Define fixed constants in Q`Q` by integer formulas:
   - `C = ((187548 << Q) + (1 << 21)) >> 22`
   - `S = ((3346570 << Q) + (1 << 21)) >> 22`

These formulas quantize Q22 approximations to `0.044715` and `sqrt(2/pi)`. For the default `Q = 12`, they produce `C = 183` and `S = 3268`.

3. Form the tanh argument:
   - `u = sat_data(xg + qmul(C, x3))`
   - `v = sat_data(qmul(S, u))`

4. Approximate tanh in Q`Q` by the rational function
   - `tanh(z) ≈ z * (27 + z^2) / (27 + 9*z^2)`
   evaluated in fixed-point as follows:
   - `v2 = qmul(v, v)`
   - `num = v * (27*ONE + v2)`
   - `den = 27*ONE + 9*v2`
   - `t = min(ONE, max(-ONE, div_trunc_zero(num, den)))`

No extra saturation is applied to `v2`, `num`, or `den` before `div_trunc_zero`.

5. Complete GELU:
   - `g = ONE + t`
   - `h = qmul(xg, g)`
   - `gelu_i = sat_data(h >> 1)`

`h >> 1` is an arithmetic right shift by 1, again truncating toward negative infinity.

The output of the activation stage is:
- `act[i] = gelu_i`

These integer equations define the approximation exactly. Clamping the rational result to [-ONE, ONE] preserves the range of tanh; it prevents a large negative input from turning into a positive activation.

### 4. Second affine projection

For each output lane `k`:

- Start with `acc2 = b2[k]`
- For `i = 0 .. INTERMEDIATE_SIZE-1`, update:
  - `acc2 = acc2 + qmul(w2[k][i], act[i])`
- After all terms are accumulated, compute:
  - `y[k] = sat_data(acc2)`

No saturation occurs inside the sum before the final `sat_data(acc2)`.

### 5. Pack output

Pack `y[0]` through `y[HIDDEN_SIZE-1]` into `out_hidden_states` using the same vector element order as `in_hidden_states`.

All returned output elements are the `DATA_W`-bit two's-complement encodings of the signed integers `y[k]`.

## Reset Behavior

Reset is active-high and asynchronous, takes priority over transfers, and discards any pending output. After reset:
- `out_valid = 0`
- `out_hidden_states = 0`
- the block retains no architectural state from prior transactions

The functional mapping is stateless: each accepted input transaction is independent of all previous transactions.
