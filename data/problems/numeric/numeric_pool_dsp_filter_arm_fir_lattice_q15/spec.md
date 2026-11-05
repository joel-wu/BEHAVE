# Q15 FIR Lattice Filter

This module implements one-sample-at-a-time processing for a fixed 4-stage FIR lattice filter in Q15 signed fixed-point arithmetic. Each accepted input sample is propagated through the lattice recurrence using four stored backward-error state values and four external Q15 reflection coefficients. Every stage performs a signed Q15×Q15 multiply, an arithmetic right shift by 15 bits, an addition, and 16-bit signed saturation exactly at each stage update. The module emits one Q15 output sample for every accepted input sample and updates the internal lattice state for the next transaction.

## Interface

### RTL interface

```systemverilog
module q15_fir_lattice_4stage (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y,

    output logic        coeff_req_valid,
    output logic [1:0]  coeff_req_addr,
    input  logic        coeff_req_ready,
    input  logic        coeff_resp_valid,
    input  logic [15:0] coeff_resp_rdata
);
```

### Behavior interface

```python
class Q15FirLattice4Stage:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x):
        pass
```

The behavior model contains a mirrored external memory list attribute named `coeff` with depth 4. Returned output dictionaries use key `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.coeff is the external read-only view of four unsigned 16-bit words. Standalone
reset clears self.state and the unbound coefficient placeholder to four zeros. The
environment binds its configured coefficient image before processing; zeros are not a
restriction on that image. Memory-request pins are not return fields.

## Modules

- `q15_fir_lattice_4stage`: top-level 4-stage Q15 FIR lattice filter with one read-only coefficient memory port.

## Functional Behavior

The module processes one signed Q15 input sample per accepted transaction.

### Data types

- `in_x` is a 16-bit two's-complement signed integer in Q15 format.
- `out_y` is a 16-bit two's-complement signed integer in Q15 format.
- `coeff[0]` through `coeff[3]` are 16-bit two's-complement signed Q15 reflection coefficients read from the external coefficient memory.
- Architectural state consists of four signed 16-bit values:
  - `state[0]` stores `g0(n-1)`
  - `state[1]` stores `g1(n-1)`
  - `state[2]` stores `g2(n-1)`
  - `state[3]` stores `g3(n-1)`

For each stage, multiplication uses signed operands. The product is arithmetically right-shifted by 15 bits. No rounding is applied before the shift; the low 15 product bits are discarded. The shifted product is added to a signed 16-bit value using an unbounded mathematical sum, then saturated to the signed 16-bit range [-32768, 32767].

Define `sat16(x)` as:
- `32767` if `x > 32767`
- `-32768` if `x < -32768`
- otherwise `x`

Define `mul_q15(a, b)` as the signed arithmetic result of `(a * b) >> 15`.

### Coefficient access

The environment supplies a read-only coefficient image with four arbitrary
16-bit values, fixed for the run. Coefficients are not restricted to zero.
Each sample reads addresses 0, 1, 2 and 3 in that order. A request transfers
on a rising edge with `coeff_req_valid && coeff_req_ready`; valid and address
must remain stable while blocked. Each accepted request receives exactly one
ordered response, at least one cycle later, indicated by `coeff_resp_valid`.
There is no response backpressure. The arithmetic uses the returned values,
not the previous sample's coefficients or a fixed internal substitute.

Inputs and outputs use their respective valid/ready handshakes. One accepted
input produces one output, in order. Output valid and data remain stable
while blocked; no fixed latency is required. Reset cancels outstanding
requests and responses in both the DUT and memory environment; the coefficient
image is re-bound unchanged for the new run.

### Per-sample lattice recurrence

Let:

- `f0 = signed(in_x)`
- `g0_prev = state[0]`
- `g1_prev = state[1]`
- `g2_prev = state[2]`
- `g3_prev = state[3]`
- `k1 = signed(coeff[0])`
- `k2 = signed(coeff[1])`
- `k3 = signed(coeff[2])`
- `k4 = signed(coeff[3])`

Then compute the four stages in this exact order:

Stage 1:
- `f1 = sat16(f0 + mul_q15(g0_prev, k1))`
- `g1 = sat16(g0_prev + mul_q15(f0, k1))`

Stage 2:
- `f2 = sat16(f1 + mul_q15(g1_prev, k2))`
- `g2 = sat16(g1_prev + mul_q15(f1, k2))`

Stage 3:
- `f3 = sat16(f2 + mul_q15(g2_prev, k3))`
- `g3 = sat16(g2_prev + mul_q15(f2, k3))`

Stage 4:
- `f4 = sat16(f3 + mul_q15(g3_prev, k4))`
- `g4 = sat16(g3_prev + mul_q15(f3, k4))`

The output sample is:
- `y = f4`

After the output value is determined, update the architectural state simultaneously to:
- `state[0] := f0`
- `state[1] := g1`
- `state[2] := g2`
- `state[3] := g3`

`g4` is computed as part of the fourth-stage recurrence but is not stored, matching a 4-stage FIR lattice whose state holds only the previous backward errors needed by the next input sample.

### Output encoding

`out_y` is the 16-bit two's-complement encoding of `y`.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst = 1`:
- `in_ready` shall be `0`
- `out_valid` shall be `0`
- `out_y` shall be `0`
- `coeff_req_valid` shall be `0`
- `coeff_req_addr` shall be `0`
- architectural state shall be:
  - `state[0] = 0`
  - `state[1] = 0`
  - `state[2] = 0`
  - `state[3] = 0`
