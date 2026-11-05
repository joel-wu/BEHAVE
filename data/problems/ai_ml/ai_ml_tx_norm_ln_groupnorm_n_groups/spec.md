# ai_ml_tx_norm_ln_groupnorm_n_groups

This block performs per-transaction group normalization on a fixed-width vector of signed integer channels. The input vector is partitioned into equal-sized groups, each group’s mean and variance are computed in integer arithmetic, each channel is normalized by the integer square root of `variance + epsilon`, and then an affine transform using per-channel `gamma` and `beta` is applied. The design is a single-clock 1:1 transaction mapper: one accepted input transaction produces exactly one output transaction containing the normalized vector.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_norm_ln_groupnorm_n_groups (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_x,
    input  logic [127:0] in_gamma,
    input  logic [127:0] in_beta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);
```

Port interpretation:

- `in_x` contains 8 signed 16-bit channels packed little-endian by lane: lane `c` is `in_x[c*16 +: 16]`.
- `in_gamma` contains 8 signed 16-bit affine scale coefficients packed with the same lane order.
- `in_beta` contains 8 signed 16-bit affine bias coefficients packed with the same lane order.
- `out_y` contains 8 signed 16-bit result channels packed with the same lane order.

Fixed architectural constants:

- Number of channels: 8
- Number of groups: 2
- Group size: 4 contiguous channels per group
- Data width per channel: 16 bits signed two’s complement
- Parameter width per channel: 16 bits signed two’s complement
- Fraction bits: 8
- Epsilon: 1

Group membership is fixed:

- Group 0: channels 0, 1, 2, 3
- Group 1: channels 4, 5, 6, 7

Reset is active-high asynchronous.

### Behavior interface

```python
class ai_ml_tx_norm_ln_groupnorm_n_groups:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x, in_gamma, in_beta):
        pass
```

`process()` arguments are unsigned packed bit-vectors matching the RTL ports `in_x`, `in_gamma`, and `in_beta`. It returns one dict per accepted input transaction:

```python
{"out_y": <128-bit unsigned packed result>}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_norm_ln_groupnorm_n_groups`: top-level group-normalization datapath and handshake control.

## Functional Behavior

Each accepted input transaction produces exactly one output transaction.

All per-lane values extracted from `in_x`, `in_gamma`, and `in_beta` are interpreted as signed 16-bit two’s-complement integers.

For each group `g`, with channels `c = 4*g + i` for `i = 0..3`, perform the following steps in the order given.

1. **Group sum**

   Let:

   - `x0`, `x1`, `x2`, `x3` be the four signed input channels of the group.

   Compute:

   - `sum_g = x0 + x1 + x2 + x3`

   This is exact integer addition with no saturation.

2. **Group mean**

   Compute the integer mean by truncating toward zero:

   - `mean_g = trunc_to_zero(sum_g / 4)`

   Examples:
   - if `sum_g = 7`, `mean_g = 1`
   - if `sum_g = -7`, `mean_g = -1`
   - if `sum_g = -8`, `mean_g = -2`

3. **Centered values and variance numerator**

   For each lane in the group:

   - `centered_i = xi - mean_g`

   Then compute:

   - `sqsum_g = centered_0*centered_0 + centered_1*centered_1 + centered_2*centered_2 + centered_3*centered_3`

   This is exact integer arithmetic with no saturation.

4. **Group variance**

   Compute the integer variance by truncating toward zero:

   - `var_g = trunc_to_zero(sqsum_g / 4)`

   Since `sqsum_g >= 0`, this is the same as floor division.

5. **Integer denominator**

   Compute:

   - `den_in_g = var_g + 1`

   Then compute the integer square root:

   - `den_g = isqrt(den_in_g)`

   where `isqrt(n)` is the greatest integer `r` such that `r*r <= n`.

   `den_in_g` is always positive for all legal inputs because `var_g >= 0` and epsilon is `1`, so `den_g >= 1` for all legal inputs. The mathematical result for `den_g == 0` is therefore unreachable and does not affect the contract.

   Examples:
   - `isqrt(0) = 0`
   - `isqrt(1) = 1`
   - `isqrt(2) = 1`
   - `isqrt(15) = 3`
   - `isqrt(16) = 4`

6. **Normalized value**

   For each lane in the group, compute a fixed-point normalized value in Q8:

   - `norm_num = centered_i * 2^8`
   - `norm_i = trunc_to_zero(norm_num / den_g)`

   `norm_i` is an integer representing a Q8 value.

7. **Affine transform**

   For each channel `c`, let `gamma_c` and `beta_c` be the signed 16-bit per-channel parameters.

   `gamma_c` and `beta_c` are both Q8 signed integers. `256` represents `+1.0`, `128` represents `+0.5`, and `-256` represents `-1.0`.

   First multiply two Q8 quantities:

   - `prod_c = gamma_c * norm_i`

   `prod_c` is Q16.

   Then narrow by removing 8 fractional bits with truncation toward zero:

   - `scaled_c = trunc_to_zero(prod_c / 2^8)`

   `scaled_c` is Q8.

   Add the bias term, which is also Q8:

   - `affine_c = scaled_c + beta_c`

   `affine_c` is Q8.

   Then narrow once more by removing 8 fractional bits with truncation toward zero:

   - `y_narrowed_c = trunc_to_zero(affine_c / 2^8)`

   `y_narrowed_c` is the unbounded signed integer narrowed result. (No saturation step is applied here or anywhere downstream; the next stage performs a 16-bit wraparound — see step 8.)

8. **Final result width conversion**

   The final output lane is the low 16 bits of `y_narrowed_c` in two’s-complement representation:

   - `out_lane_c = y_narrowed_c mod 2^16`

   Equivalently, convert `y_narrowed_c` to signed 16-bit two’s-complement by wraparound, not saturation.

   Examples:
   - if `y_narrowed_c = 0`, output bits are `0x0000`
   - if `y_narrowed_c = -1`, output bits are `0xFFFF`
   - if `y_narrowed_c = 32767`, output bits are `0x7FFF`
   - if `y_narrowed_c = -32768`, output bits are `0x8000`
   - if `y_narrowed_c = 32768`, output bits are `0x8000`
   - if `y_narrowed_c = -32769`, output bits are `0x7FFF`

   Pack `out_lane_c` back into `out_y` lane `c`.

Definitions required for bit-exact implementation:

- `trunc_to_zero(a / b)` means integer division rounded toward zero, not toward negative infinity.
- For division by powers of two, the same truncation-toward-zero rule applies. Examples:
  - `trunc_to_zero(513 / 256) = 2`
  - `trunc_to_zero(-513 / 256) = -2`
  - `trunc_to_zero(255 / 256) = 0`
  - `trunc_to_zero(-255 / 256) = 0`

Worked example for one group:

- Inputs: `[1, 2, 3, 4]`
- `sum = 10`
- `mean = trunc_to_zero(10 / 4) = 2`
- `centered = [-1, 0, 1, 2]`
- `sqsum = 1 + 0 + 1 + 4 = 6`
- `var = trunc_to_zero(6 / 4) = 1`
- `den = isqrt(1 + 1) = isqrt(2) = 1`
- `norm = [-256, 0, 256, 512]` in Q8
- If `gamma = 256` and `beta = 0` for every lane, then:
  - `scaled = trunc_to_zero((256 * norm) / 256) = norm`
  - `y_narrowed = trunc_to_zero(norm / 256) = [-1, 0, 1, 2]`
- Final output lane bit patterns for that group are `[0xFFFF, 0x0000, 0x0001, 0x0002]`.

## Reset Behavior

On assertion of `rst`:

- `in_ready` is high because the block is idle and able to accept an input transaction.
- `out_valid` is low.
- `out_y` is all zeros.
- The only architectural state is the stored pending output word and the busy flag:
  - busy flag = `0`
  - stored output word = `0`

After reset is deasserted, the block accepts one input transaction when idle, stores the computed 128-bit result, and presents exactly one output transaction. While holding an unconsumed output transaction, the block may deassert `in_ready`.
