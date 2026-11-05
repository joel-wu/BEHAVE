# AiMlSsmRwkvRwkv6ChannelMix

AiMlSsmRwkvRwkv6ChannelMix is a single-token, stateful interpolation sub-operator derived from the RWKV6 `DDLerpLinear` primitive. For each accepted input transaction, the block forms a per-channel delta between the stored previous token and the current token, computes an elementwise mixed vector `x + delta * mu`, and emits that mixed vector. The block retains the current token as state for the next transaction.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_rwkv_rwkv6_channel_mix #(
    parameter int WIDTH = 32
) (
    input  logic             clk,
    input  logic             rst,

    input  logic             in_valid,
    output logic             in_ready,
    input  logic [WIDTH-1:0] in_x,
    input  logic [WIDTH-1:0] in_mu,

    output logic             out_valid,
    input  logic             out_ready,
    output logic [WIDTH-1:0] out_y
);
```

`in_x`, `in_mu`, and `out_y` are signed two's-complement integers carried as `WIDTH`-bit port bit patterns. `WIDTH` is 32.

### Behavior interface

```python
class AiMlSsmRwkvRwkv6ChannelMix:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x, in_mu):
        pass
```

`process()` returns a dict with one key:

- `out_y`: unsigned 32-bit bit pattern of the signed result

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_rwkv_rwkv6_channel_mix`: top-level RWKV6 channel-mix interpolation block.

## Functional Behavior

The block processes one scalar channel per accepted input transaction.

The block contains one architectural state register:

- `prev_x`: signed 32-bit previous-token value

The fixed configuration is WIDTH=32. Every input and output scalar is signed Q16 (raw value divided by 65536). Intermediate subtraction and multiplication are exact signed integer operations; only the final output wraps modulo `2^32`. `trunc32(v)` means keep the low 32 bits of `v`. When interpreting a truncated value as signed, bit 31 is the sign bit.

### 1. Delta computation

For the accepted transaction, interpret `in_x` and `in_mu` as signed 32-bit integers `x` and `mu`.

Compute:

- `delta = prev_x - x`

Keep the full 33-bit signed difference; do not truncate it before multiplication.

### 2. Interpolated value

Compute the signed product:

- `prod = delta * mu`

Then compute:

- `y = x + TZ(prod / 65536)`

`prod` is formed in unbounded precision. The final result `y` is truncated once to 32 bits with wraparound modulo `2^32`.

`TZ` rounds toward zero. There is no saturation. In particular mu=32768 mixes half of the previous and current samples; mu=0 selects the current sample and mu=65536 selects the previous sample. Other signed Q16 coefficients extrapolate. The source linear projection is fixed to identity here; this task does not implement the complete RWKV channel-mixing network.

### 3. Output transaction

Each accepted input transaction produces exactly one output transaction:

- `out_y = trunc32(y)` encoded as an unsigned 32-bit bit pattern

### 4. State update

After the output is computed from the old state, update:

- `prev_x := x`

The current transaction always reads the pre-update `prev_x`.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `prev_x = 0`

When no output transaction is being presented, `out_valid = 0`. `out_y` is not architecturally defined while `out_valid = 0`.
On reset pending output is discarded and out_y is zero. Input/output transfers occur on rising edges with valid and ready both high; a pending output remains stable under backpressure.
