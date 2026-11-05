# FirQ15Sample

FirQ15Sample is a single-sample Q15 finite impulse response filter kernel. Each accepted input transaction consumes one signed 16-bit sample in 1.15 format, shifts it into an internal delay line of fixed length 8, computes one FIR output using 8 signed 16-bit coefficients in 1.15 format, accumulates all products in a signed 64-bit accumulator with full product precision, then truncates by arithmetic right shift by 15 bits and saturates to a signed 16-bit 1.15 result. The kernel implements the direct-form sample-by-sample FIR recurrence with persistent state across transactions.

## Interface

### RTL interface

```systemverilog
module fir_q15_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);
```

### Behavior interface

```python
class FirQ15Sample:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample):
        pass
```

The `process()` return value is a dict with key `out_sample`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fir_q15_sample`: top-level FIR kernel with one 8-sample state buffer and one output per accepted input.

## Functional Behavior

The kernel has a fixed tap count of 8.

`in_sample` is interpreted as a signed 16-bit two's-complement integer representing a Q15 value in 1.15 format.

The coefficient vector is fixed and architecturally visible through the specification. Coefficients are stored in tap order `coeff[0]` through `coeff[7]`, each interpreted as a signed 16-bit two's-complement integer in 1.15 format:

- `coeff[0] = 0x1000`
- `coeff[1] = 0x1000`
- `coeff[2] = 0x1000`
- `coeff[3] = 0x1000`
- `coeff[4] = 0x1000`
- `coeff[5] = 0x1000`
- `coeff[6] = 0x1000`
- `coeff[7] = 0x1000`

These eight coefficients implement an 8-point moving-average FIR scaled by 1/8 in Q15.

The architectural state is an 8-element delay line `state[0]` through `state[7]`, each a signed 16-bit sample in 1.15 format. Before each output computation, the state is updated as follows using old-state values:

- `new_state[0] = in_sample`
- `new_state[1] = state[0]`
- `new_state[2] = state[1]`
- `new_state[3] = state[2]`
- `new_state[4] = state[3]`
- `new_state[5] = state[4]`
- `new_state[6] = state[5]`
- `new_state[7] = state[6]`

The output is computed from `new_state`, not from the old state. The dot product is:

- `acc = (new_state[0] * coeff[0]) + (new_state[1] * coeff[1]) + (new_state[2] * coeff[2]) + (new_state[3] * coeff[3]) + (new_state[4] * coeff[4]) + (new_state[5] * coeff[5]) + (new_state[6] * coeff[6]) + (new_state[7] * coeff[7])`

Each multiplication is signed 16-bit by signed 16-bit, producing a signed 32-bit mathematical product in 2.30 format. The sum is accumulated exactly in signed 64-bit arithmetic. No intermediate truncation is applied during accumulation.

After the full sum is complete, the accumulator is converted from 2.30 to 1.15 by arithmetic right shift by 15 bits:

- `shifted = acc >>> 15`

This shift discards the low 15 bits. No rounding is applied.

The shifted result is then saturated to the signed 16-bit range `[-32768, 32767]`. This specification fixes the saturation operation as a clamp:

- `out_sample = min(max(shifted, -32768), 32767)`

`out_sample` is driven on the output as a 16-bit two's-complement bit pattern.

One accepted input transaction always produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_sample = 0x0000`

Architectural state after reset:

- `state[0] = 0x0000`
- `state[1] = 0x0000`
- `state[2] = 0x0000`
- `state[3] = 0x0000`
- `state[4] = 0x0000`
- `state[5] = 0x0000`
- `state[6] = 0x0000`
- `state[7] = 0x0000`
