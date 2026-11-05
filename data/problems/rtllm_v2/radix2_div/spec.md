# Eight-bit Radix-2 Divider

Divide unsigned or two's-complement eight-bit operands using one capture edge and eight radix-2 iterations. The native interface has a request-valid input and a held result-valid output; there is no input-ready signal. The `res_ready` input is present in the original RTL and testbench, although omitted from the original description's port list.

## Interface

### RTL interface

```systemverilog
module radix2_div (
    input wire clk,
    input wire rst,
    input wire [7:0] dividend,
    input wire [7:0] divisor,
    input wire sign,
    input wire opn_valid,
    output reg res_valid,
    input wire res_ready,
    output wire [15:0] result
);
```

### Behavior interface

```python
class Radix2Divider:
    def __init__(self): ...
    def reset(self): ...
    def process(self, dividend, divisor, sign, opn_valid, res_ready): ...
```

Each call returns `{"res_valid": valid, "result": value}` for the settled pins immediately before one non-reset rising edge, then advances the state. These are pin observations: a call while busy is not a newly accepted divide request. Reset edges have no logical observation record.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `res_valid`: 1.
- `result`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `radix2_div`: the native eight-bit signed/unsigned iterative divider.

## Functional Behavior

All inputs are unsigned bit patterns of the widths shown. `sign=1` selects two's-complement interpretation; quotient negation wraps to eight bits. For a complete operation with stable sign, `result[15:8]` is the remainder and `result[7:0]` the quotient. Signed division truncates toward zero; a nonzero remainder has the dividend's sign. Division by zero returns quotient zero and the original dividend as remainder. The signed overflow case -128 / -1 returns remainder zero and quotient `0x80`.

At a rising edge, a request is captured exactly when `opn_valid=1`, the divider is idle and `res_valid=0`. Busy-time requests are ignored. Operands are saved at capture; subsequent operand changes do not affect the operation. Starting with capture at edge k, eight iterations finish at edge k+8. The first pre-edge observation with `res_valid=1` is at edge k+9. The result is held until an edge with `res_valid && res_ready`; consuming a result does not capture a new request on the same edge. Held `opn_valid` can start a new operation on the following edge.

The native `sign` pin is **not latched**: it selects magnitudes at capture and also controls the combinational sign correction of the saved partial/final result on every sample. Hold it stable through result consumption for ordinary signed division. Changing it is defined, not prohibited: the recurrence below specifies the observed bits. `result` exposes the working shift register even when `res_valid=0`; both output pins are observed on every non-reset sample. There is no additional backpressure rule on these pin observations.

The following recurrence specifies intermediate values and control priority. Let `D`, `V`, `S`, `N`, `c`, `busy` and `valid` be the saved dividend, saved divisor, 16-bit working register, nine-bit negative divisor, iteration count, busy bit and result-valid bit. Initially all externally relevant state is zero. Before updating:

- Let `r=S[15:8]`, `q=S[7:0]`. Negate r modulo 256 if `sign && D[7]`; negate q modulo 256 if `sign && (D[7] XOR V[7])`. Emit `{r,q}` and `valid`.
- On capture, save D and V. Set S to twice the eight-bit magnitude of dividend (negated modulo 256 only when `sign && dividend[7]`), c=1 and busy=1. Set N to `256 | divisor` when `sign && divisor[7]`, otherwise to `(-divisor) mod 512`.
- Otherwise, while busy, form the ten-bit sum `T=S[15:8]+N`, take carry=T[9], and select `m=T[8:0]` if carry is one, otherwise `m=S[15:8]`. If old c=8, set `S={m[7:0],S[7:1],carry}`, c=0 and busy=0. Otherwise set `S={m[6:0],S[7:1],carry,0}` and increment c.
- Independently, set valid=1 when old c=8; otherwise clear valid if old valid and res_ready are both one; otherwise retain it. Completion takes priority over result consumption.

## Reset Behavior

`rst` is synchronous and active high. A rising edge with rst=1 clears S, the saved operands, count, busy and res_valid, cancelling any operation or unconsumed result. The negative-divisor register is private and overwritten at every capture before it can affect an output; its reset value is not observable. Result is zero after the reset edge, for either sign value.
