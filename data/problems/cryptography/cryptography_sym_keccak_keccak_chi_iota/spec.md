# Keccak-f[1600] Chi and Iota

Apply the complete Chi and Iota stages of one Keccak-f[1600] round to 25 intermediate lanes. Theta, Rho and Pi are outside this sub-operator.

## Interface

### RTL interface

```systemverilog
module keccak_chi_iota (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,
    input  logic [63:0]  in_b4,
    input  logic [63:0]  in_b5,
    input  logic [63:0]  in_b6,
    input  logic [63:0]  in_b7,
    input  logic [63:0]  in_b8,
    input  logic [63:0]  in_b9,
    input  logic [63:0]  in_b10,
    input  logic [63:0]  in_b11,
    input  logic [63:0]  in_b12,
    input  logic [63:0]  in_b13,
    input  logic [63:0]  in_b14,
    input  logic [63:0]  in_b15,
    input  logic [63:0]  in_b16,
    input  logic [63:0]  in_b17,
    input  logic [63:0]  in_b18,
    input  logic [63:0]  in_b19,
    input  logic [63:0]  in_b20,
    input  logic [63:0]  in_b21,
    input  logic [63:0]  in_b22,
    input  logic [63:0]  in_b23,
    input  logic [63:0]  in_b24,
    input  logic [4:0]   in_round,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [63:0]  out_a0,
    output logic [63:0]  out_a1,
    output logic [63:0]  out_a2,
    output logic [63:0]  out_a3,
    output logic [63:0]  out_a4,
    output logic [63:0]  out_a5,
    output logic [63:0]  out_a6,
    output logic [63:0]  out_a7,
    output logic [63:0]  out_a8,
    output logic [63:0]  out_a9,
    output logic [63:0]  out_a10,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a14,
    output logic [63:0]  out_a15,
    output logic [63:0]  out_a16,
    output logic [63:0]  out_a17,
    output logic [63:0]  out_a18,
    output logic [63:0]  out_a19,
    output logic [63:0]  out_a20,
    output logic [63:0]  out_a21,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a24
);
```

Transfers occur on rising clock edges with valid and ready both high and rst low. The input producer holds the whole request stable while stalled. The block holds the whole response, including `out_error`, stable while stalled. Every accepted request produces exactly one ordered response unless reset cancels it. No fixed latency or pipeline architecture is required.

### Behavior interface

```python
class KeccakChiIota:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7, in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15, in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23, in_b24, in_round): ...
```

Arguments and returned values are raw nonnegative port bit patterns. One call returns a dictionary containing `out_a0`, `out_a1`, `out_a2`, `out_a3`, `out_a4`, `out_a5`, `out_a6`, `out_a7`, `out_a8`, `out_a9`, `out_a10`, `out_a11`, `out_a12`, `out_a13`, `out_a14`, `out_a15`, `out_a16`, `out_a17`, `out_a18`, `out_a19`, `out_a20`, `out_a21`, `out_a22`, `out_a23`, `out_a24` and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_a0`, `out_a1`, `out_a10`, `out_a11`, `out_a12`, `out_a13`, `out_a14`,
  `out_a15`, `out_a16`, `out_a17`, `out_a18`, `out_a19`, `out_a2`, `out_a20`,
  `out_a21`, `out_a22`, `out_a23`, `out_a24`, `out_a3`, `out_a4`, `out_a5`, `out_a6`,
  `out_a7`, `out_a8`, `out_a9`: 64.
- `out_error`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `keccak_chi_iota`: checked integer sub-operator and ready/valid transport.

## Functional Behavior

`in_round` is an unsigned round index and must be in 0..23. Every 64-bit lane bit pattern is valid.

An out-of-range request returns `out_error=1` and **zero in every numerical output field**. A valid request returns `out_error=0` and the result below. The error flag is ordinary response data, not a missing transaction. No invalid input is silently clamped, normalized or evaluated as a valid operation.

Use lane order `index=x+5*y`, for x,y=0..4. Let `B[x,y]` be the corresponding input. Compute all rows from the original input values:

```text
A[x,y] = B[x,y] xor ((not B[(x+1) mod 5,y]) and B[(x+2) mod 5,y])
A[0,0] = A[0,0] xor RC[in_round]
```

NOT is restricted to 64 bits. Return A in the same lane order. `RC[0..23]`, in order, is:

```text
0x0000000000000001
0x0000000000008082
0x800000000000808A
0x8000000080008000
0x000000000000808B
0x0000000080000001
0x8000000080008081
0x8000000000008009
0x000000000000008A
0x0000000000000088
0x0000000080008009
0x000000008000000A
0x000000008000808B
0x800000000000008B
0x8000000000008089
0x8000000000008003
0x8000000000008002
0x8000000000000080
0x000000000000800A
0x800000008000000A
0x8000000080008081
0x8000000000008080
0x0000000080000001
0x8000000080008008
```

No round state is retained and no other round stage is applied.

All arithmetic is integer arithmetic. Signed results use the output width's two's-complement encoding; specified shifts of negative values are arithmetic. There is no unspecified floating-point behavior.

## Reset Behavior

Reset is synchronous, active-high. Reset clears `out_valid`, `out_error` and all output payload fields and cancels pending responses. No transfers are counted while reset is asserted. The operation has no mathematical state across requests. After reset it accepts new requests normally; idle payload values after subsequent transfers are otherwise unspecified.
