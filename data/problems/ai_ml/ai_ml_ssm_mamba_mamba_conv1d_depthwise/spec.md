# Mamba Conv1D Depthwise

This block implements one channel of Mamba's causal depthwise convolution, before bias and SiLU. Each accepted sample advances a retained window and produces its dot product with the kernel. The same transaction may replace one kernel coefficient before computing that result. Parallel instances implement independent channels.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_mamba_mamba_conv1d_depthwise #(
    parameter int D_CONV = 4,
    parameter int DATA_W = 32
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [DATA_W-1:0]     in_x,

    input  logic                  cfg_valid,
    input  logic                  cfg_we,
    input  logic [1:0]            cfg_addr,
    input  logic signed [DATA_W-1:0] cfg_wdata,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [DATA_W-1:0]     out_y
);
```

Supported parameters are `1 <= D_CONV <= 4` and `8 <= DATA_W <= 32`.
All `cfg_*` pins belong to the input transaction: a write occurs only on an
edge with `in_valid && in_ready && cfg_valid && cfg_we`. Address `k < D_CONV`
replaces `weight[k]`, and the replacement is used by that same sample.
Addresses outside the kernel are ignored. No configuration or convolution
state changes without an input transfer, regardless of the idle pin values.

Port interpretation:

- `in_x` is one signed `DATA_W`-bit sample, carried as a `DATA_W`-bit two's-complement bit pattern.
- `out_y` is one signed `DATA_W`-bit result, carried as a `DATA_W`-bit two's-complement bit pattern interpreted as an unsigned port value.
- Kernel coefficients are writable architectural state:
  - `weight[k]` for `k = 0 .. D_CONV-1`
  - `weight[0]` multiplies the oldest sample in the convolution window
  - `weight[D_CONV-1]` multiplies the newest sample in the convolution window
- Reset initializes every coefficient to one. The default instance has four
  taps and 32-bit samples and coefficients.

### Behavior interface

```python
class MambaConv1DDepthwise:
    def __init__(self, d_conv=4, data_w=32): ...
    def reset(self): ...
    def process(self, in_x, cfg_valid, cfg_we, cfg_addr, cfg_wdata): ...
```

`process()` returns a dictionary with one key:

- `{"out_y": <uint DATA_W bit pattern>}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: DATA_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor d_conv and data_w match RTL D_CONV and DATA_W.

## Modules

- `ai_ml_ssm_mamba_mamba_conv1d_depthwise`: top-level single-channel streaming depthwise causal convolution block with internal convolution state.

## Functional Behavior

All arithmetic inputs and state samples are interpreted as signed two's-complement integers of width `DATA_W`. The output is returned as the unsigned `DATA_W`-bit encoding of the final signed result.

The block is a 1:1 streaming transform: every accepted input transaction produces exactly one output transaction.

Architectural state:

- `conv_state[k]` for `k = 0 .. D_CONV-1`
- `conv_state[0]` is the oldest retained sample
- `conv_state[D_CONV-1]` is the newest retained sample

For each accepted input transaction:

1. Decode the input sample:
   - `x_new = signed(in_x)`

2. Form the updated causal window:
   - `next_state[k] = conv_state[k+1]` for `k = 0 .. D_CONV-2`
   - `next_state[D_CONV-1] = x_new`

3. If `cfg_valid && cfg_we && cfg_addr < D_CONV`, set
   `weight[cfg_addr] := signed(cfg_wdata)`. Use the updated weight below.

4. Compute the convolution sum using the updated window and effective weights:
   - `preact = sum(next_state[k] * weight[k] for k in 0 .. D_CONV-1)`

5. The signed integer `preact` is narrowed to `DATA_W` bits by modulo `2^DATA_W` wrap and emitted:
   - `out_y = preact mod 2^DATA_W`

6. Commit `conv_state = next_state`.

With the boxcar default `weight = [1, 1, 1, 1]`, the block computes a 4-tap moving sum over the causal window. Cfg writes can replace any tap to specialize the kernel.

Both endpoints transfer on `valid && ready` at a rising clock edge. An offered
input, including its configuration fields, remains stable until accepted.
An output remains valid and unchanged while stalled; responses preserve input
order. Input and output transfers may occur together. Latency is not fixed.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst == 1`, the architectural state after the active clock edge is:

- `conv_state[k] = 0` for every `k = 0 .. D_CONV-1`
- `weight[k] = 1` for every `k = 0 .. D_CONV-1` (boxcar default)

Output protocol state after reset:

- `out_valid = 0`
- `in_ready = 1`

When `out_valid = 0`, `out_y` is don't-care. A deterministic implementation may drive `out_y = 0`.
