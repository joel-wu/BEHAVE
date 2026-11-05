# Four-Phase Four-Tap Configurable FIR Bank

Push one sample and evaluate the selected phase of a configurable polyphase FIR bank, preserving its shared delay-line state.

## Interface

### RTL interface

```systemverilog
module firpfb_4phase_4tap #(
    parameter logic [255:0] H_FLAT = 256'h0003000200010001ffff0002fffe00030002ffff0001fffe0004000300020001,
    parameter logic [15:0] SCALE = 16'd1
) (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_x,
    input logic [1:0] in_i,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
```

### Behavior interface

```python
class FirPfb4Phase4Tap:
    def __init__(self, H_FLAT=0x0003000200010001ffff0002fffe00030002ffff0001fffe0004000300020001, SCALE=1): ...
    def reset(self): ...
    def process(self, in_x, in_i): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_y": ...}`, whose value is a Python integer holding the specified output
port bit pattern, including for a negative fixed-point result.

### Transaction protocol

Payloads use unsigned bit patterns unless stated otherwise. An input is accepted on a rising edge with in_valid && in_ready, and an output is emitted with out_valid && out_ready. Once presented, valid and its payload remain stable until accepted; reset cancels pending transfers. Latency is unconstrained. Each accepted input transaction produces exactly one output transaction, in input order.

## Modules

`firpfb_4phase_4tap` is the complete top-level module.

## Functional Behavior

There are four phases and four taps per phase. H_FLAT contains 16 signed two's-complement 16-bit coefficients h[0] through h[15], with h[k] in bits [16*k+15:16*k]. SCALE is a signed 16-bit two's-complement multiplier. Both are fixed configuration parameters, not stream state. The default coefficients in increasing k are [1, 2, 3, 4, -2, 1, -1, 2, 3, -2, 2, -1, 1, 1, 2, 3], and the default scale is 1.

Each request pushes x=the signed value of in_x into a shared sample history and selects phase i=in_i. With x0 the new sample and x1,x2,x3 the previous three accepted samples (initially zero), return the signed integer

```
y = SCALE * (h[i]*x0 + h[i+4]*x1 + h[i+8]*x2 + h[i+12]*x3)
```

The single shared history advances once per accepted input, regardless of phase. Only acceptance advances it; output stalls must not change it. All products and sums are exact, without rounding or saturation. The result fits signed 64 bits for every allowed coefficient/sample/scale; out_y carries its 64-bit two's-complement pattern. There is no coefficient reversal beyond this formula. This is a bounded integer realization of a configurable polyphase FIR bank, not a decimator or interpolator controller.

## Reset Behavior

Reset is synchronous active-high rst. It empties pending output; out_valid is low during reset. Reset clears all three retained samples and any pending output. H_FLAT and SCALE are configuration and remain unchanged.
