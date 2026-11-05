# SparseFirQ15Block4

This module implements one fixed-size transaction of a sparse FIR filter operating on signed Q15 data. Each accepted input transaction provides four consecutive input samples, updates an internal circular state buffer, and produces four consecutive output samples for a fixed 4-tap sparse filter. The filter uses the same arithmetic contract as the CMSIS-DSP Q15 sparse FIR kernel: each tap multiplies a 1.15 sample by a 1.15 coefficient to form a 2.30 product, the tap products are accumulated in a wrapping 32-bit 2.30 accumulator, then each accumulator is arithmetically shifted right by 15 bits and saturated to signed 16-bit 1.15 output.

## Interface

### RTL interface

```systemverilog
module sparse_fir_q15_block4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x0,
    input  logic [15:0]  in_x1,
    input  logic [15:0]  in_x2,
    input  logic [15:0]  in_x3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3
);
```

### Behavior interface

```python
class SparseFirQ15Block4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3):
        pass
```

The `process()` method returns one dict per accepted input transaction with keys:
`out_y0`, `out_y1`, `out_y2`, `out_y3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `sparse_fir_q15_block4`: top-level fixed-configuration sparse FIR block for 4-sample transactions.

## Functional Behavior

The module is a single-clock, single-input, single-output 1:1 transaction engine. Every accepted input transaction consumes exactly one 4-sample block and produces exactly one 4-sample output block.

The filter configuration is fixed:

- `blockSize = 4`
- `numTaps = 4`
- `maxDelay = 7`
- `delaySize = maxDelay + blockSize = 11`
- tap delays in coefficient order: `d[0]=0`, `d[1]=2`, `d[2]=5`, `d[3]=7`
- Q15 coefficients in coefficient order:
  - `c[0] = 0x2000` = +8192
  - `c[1] = 0xE000` = -8192
  - `c[2] = 0x1000` = +4096
  - `c[3] = 0x0800` = +2048

All input and output ports carry 16-bit two's-complement signed Q15 bit patterns. Arithmetic inside the filter interprets these ports as signed integers.

Architectural state:

- `state[0..10]`: circular sample buffer, 11 entries, each a signed 16-bit value.
- `stateIndex`: write pointer into `state`, integer range 0 to 10 inclusive. It identifies the buffer location where the first sample of the next accepted input block is written.

For each accepted input transaction, the computation is:

1. Interpret `in_x0`, `in_x1`, `in_x2`, `in_x3` as signed 16-bit integers `x_in[0..3]`.

2. Write the four input samples into the circular state buffer in order:
   - `state[stateIndex] := x_in[0]`
   - `state[(stateIndex + 1) mod 11] := x_in[1]`
   - `state[(stateIndex + 2) mod 11] := x_in[2]`
   - `state[(stateIndex + 3) mod 11] := x_in[3]`

3. Define `new_stateIndex = (stateIndex + 4) mod 11`. All reads for the current transaction use the state contents after step 2 and use `new_stateIndex` in the read-index formula.

4. For each tap `t` in coefficient order 0, 1, 2, 3, compute
   - `readBase[t] = new_stateIndex - blockSize - d[t]`
   - if `readBase[t] < 0`, add 11 once
   - equivalently, because `blockSize=4` and `0 <= d[t] <= 7`, `readBase[t] = (new_stateIndex - 4 - d[t]) mod 11`

5. For each output position `k` in 0, 1, 2, 3, gather one sample from each tap delay:
   - `s[t][k] = state[(readBase[t] + k) mod 11]`

6. For each output position `k`, form a 32-bit wrapping accumulator in coefficient order:
   - `acc[k] = wrap32(s[0][k] * c[0])`
   - `acc[k] = wrap32(acc[k] + s[1][k] * c[1])`
   - `acc[k] = wrap32(acc[k] + s[2][k] * c[2])`
   - `acc[k] = wrap32(acc[k] + s[3][k] * c[3])`

   Here each multiplication is signed 16-bit by signed 16-bit, producing an exact signed 32-bit product in 2.30 format. `wrap32` means truncation to 32-bit two's-complement after each addition. Overflow in the accumulator wraps around; it does not saturate.

7. Convert each accumulator to output Q15:
   - `tmp[k] = acc[k] >>> 15`, using arithmetic right shift
   - `y[k] = sat16(tmp[k])`

   `sat16(v)` saturates to the signed 16-bit range:
   - if `v > 32767`, result is `32767`
   - else if `v < -32768`, result is `-32768`
   - else result is `v`

8. Present outputs as unsigned 16-bit port bit patterns:
   - `out_y0 = y[0]`
   - `out_y1 = y[1]`
   - `out_y2 = y[2]`
   - `out_y3 = y[3]`

9. Commit the state update:
   - `stateIndex := new_stateIndex`
   - `state` retains the four writes from step 2 and all other entries remain unchanged.

Equivalent closed-form output equations for the fixed configuration are:

- `y[0] = sat16(( state[(new_stateIndex-4-0) mod 11] * c[0]
                + state[(new_stateIndex-4-2) mod 11] * c[1]
                + state[(new_stateIndex-4-5) mod 11] * c[2]
                + state[(new_stateIndex-4-7) mod 11] * c[3]) wrap32 >>> 15)`

- `y[1] = sat16(( state[(new_stateIndex-3-0) mod 11] * c[0]
                + state[(new_stateIndex-3-2) mod 11] * c[1]
                + state[(new_stateIndex-3-5) mod 11] * c[2]
                + state[(new_stateIndex-3-7) mod 11] * c[3]) wrap32 >>> 15)`

- `y[2] = sat16(( state[(new_stateIndex-2-0) mod 11] * c[0]
                + state[(new_stateIndex-2-2) mod 11] * c[1]
                + state[(new_stateIndex-2-5) mod 11] * c[2]
                + state[(new_stateIndex-2-7) mod 11] * c[3]) wrap32 >>> 15)`

- `y[3] = sat16(( state[(new_stateIndex-1-0) mod 11] * c[0]
                + state[(new_stateIndex-1-2) mod 11] * c[1]
                + state[(new_stateIndex-1-5) mod 11] * c[2]
                + state[(new_stateIndex-1-7) mod 11] * c[3]) wrap32 >>> 15)`

where the `state[...]` terms in these equations refer to the post-write state of the current transaction.

Worked example from reset state:

- Before the first transaction, all 11 state entries are zero and `stateIndex=0`.
- Suppose the accepted input is `in_x0=a`, `in_x1=b`, `in_x2=c`, `in_x3=d`.
- After writing, `state[0]=a`, `state[1]=b`, `state[2]=c`, `state[3]=d`, all other entries remain zero, and `new_stateIndex=4`.
- The outputs are computed from state indices:
  - `y[0]` uses indices 0, 9, 6, 4
  - `y[1]` uses indices 1, 10, 7, 5
  - `y[2]` uses indices 2, 0, 8, 6
  - `y[3]` uses indices 3, 1, 9, 7`
- Therefore only `y[2]` and `y[3]` can contain nonzero contributions from earlier-written samples in that first transaction:
  - `y[0] = sat16((a*c[0]) >>> 15)`
  - `y[1] = sat16((b*c[0]) >>> 15)`
  - `y[2] = sat16((c*c[0] + a*c[1]) wrap32 >>> 15)`
  - `y[3] = sat16((d*c[0] + b*c[1]) wrap32 >>> 15)`

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst=1`, the architectural state and outputs are set to:

- `state[0]` through `state[10]` = `16'h0000`
- `stateIndex = 0`
- `out_y0 = 16'h0000`
- `out_y1 = 16'h0000`
- `out_y2 = 16'h0000`
- `out_y3 = 16'h0000`

After reset, the module is able to accept an input transaction. No buffered partial transaction state exists.
