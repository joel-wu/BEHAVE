# VectorExp4F32

VectorExp4F32 computes four independent binary32 approximations of the natural
exponential. Each accepted four-lane vector produces one ordered four-lane
result. A deterministic fixed-point range reduction and degree-12 polynomial
define the result bits; input significands participate in the computation.

## Interface

### RTL interface

```systemverilog
module vector_exp4_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class VectorExp4F32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `vector_exp4_f32`: four-lane exponential with ordered ready/valid transport.

## Functional Behavior

Each `in_xk` and `out_yk` is an IEEE-754 binary32 bit pattern, carried as an
unsigned 32-bit word. Lane `k` depends only on `in_xk`; there is no arithmetic
state across transactions. Transfers occur on rising edges with valid and
ready both high. An offered input must remain stable until accepted. The
module holds `out_valid` and all output words while `out_ready` is low. Apart
from reset cancellation, exactly one output is required per accepted vector,
in input order; latency and internal pipelining are not prescribed.

For each lane, NaNs map to `0x7FC00000`, positive infinity to `0x7F800000`,
negative infinity to `0x00000000`, and either zero to `0x3F800000`. For a finite
nonzero input, let `x` be its exact real value. The following integer algorithm
is normative; it does not require matching a particular software `expf` bit
for bit.

1. If `x >= 89`, return positive infinity; if `x <= -104`, return positive
   zero. These cutoffs lie beyond binary32 exponential overflow and final
   gradual underflow, respectively.
2. Set `S = 2^48`, `L = 195103586505167` (nearest-even `S*ln(2)`), and
   `X = trunc(x*S)`, where truncation is toward zero. Set `n = floor(X/L)`
   and `R = X - n*L`, so `0 <= R < L`.
3. Let `C[j]` be nearest-even `S/j!`. In increasing `j=0..12` order these are:
   ```text
   281474976710656, 281474976710656, 140737488355328,
   46912496118443, 11728124029611, 2345624805922, 390937467654,
   55848209665, 6981026208, 775669579, 77566958, 7051542, 587628
   ```
   Start `P = C[12]`. For `j=11,10,...,0`, replace
   `P = C[j] + floor(R*P/S)`. Products and sums have no intermediate overflow.
4. Round the positive value `(P/S)*2^n` to binary32, nearest with ties to even.
   Preserve subnormal results (no flush-to-zero); overflow yields positive
   infinity. This single final rounding is separate from the truncations in
   step 3.

This specifies an approximation to the exponential, not correctly rounded
transcendental arithmetic. Small inputs and subnormals are included: their
result may round to one, but small nonzero inputs are not replaced by a broad
exponent bucket. All results except the canonical NaN are nonnegative.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst == 1`, the module shall return to its reset state. The reset state contains no buffered input data and no pending output transaction.

Architectural state after reset:

- no stored operands
- no stored results
- output-valid state cleared

Output port values in reset state:

- `in_ready = 1`
- `out_valid = 0`
- `out_y0 = 0x00000000`
- `out_y1 = 0x00000000`
- `out_y2 = 0x00000000`
- `out_y3 = 0x00000000`

After reset is deasserted, the first accepted input transaction is processed with no dependence on any pre-reset history.
