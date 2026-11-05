# One Keccak-f[1600] round

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module keccak_f1600_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [4:0]   in_round_idx,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,
    input  logic [63:0]  in_x4,
    input  logic [63:0]  in_x5,
    input  logic [63:0]  in_x6,
    input  logic [63:0]  in_x7,
    input  logic [63:0]  in_x8,
    input  logic [63:0]  in_x9,
    input  logic [63:0]  in_x10,
    input  logic [63:0]  in_x11,
    input  logic [63:0]  in_x12,
    input  logic [63:0]  in_x13,
    input  logic [63:0]  in_x14,
    input  logic [63:0]  in_x15,
    input  logic [63:0]  in_x16,
    input  logic [63:0]  in_x17,
    input  logic [63:0]  in_x18,
    input  logic [63:0]  in_x19,
    input  logic [63:0]  in_x20,
    input  logic [63:0]  in_x21,
    input  logic [63:0]  in_x22,
    input  logic [63:0]  in_x23,
    input  logic [63:0]  in_x24,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3,
    output logic [63:0]  out_x4,
    output logic [63:0]  out_x5,
    output logic [63:0]  out_x6,
    output logic [63:0]  out_x7,
    output logic [63:0]  out_x8,
    output logic [63:0]  out_x9,
    output logic [63:0]  out_x10,
    output logic [63:0]  out_x11,
    output logic [63:0]  out_x12,
    output logic [63:0]  out_x13,
    output logic [63:0]  out_x14,
    output logic [63:0]  out_x15,
    output logic [63:0]  out_x16,
    output logic [63:0]  out_x17,
    output logic [63:0]  out_x18,
    output logic [63:0]  out_x19,
    output logic [63:0]  out_x20,
    output logic [63:0]  out_x21,
    output logic [63:0]  out_x22,
    output logic [63:0]  out_x23,
    output logic [63:0]  out_x24
);
```

### Behavior interface

```python
class KeccakF1600Round:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_round_idx, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16, in_x17, in_x18, in_x19, in_x20, in_x21, in_x22, in_x23, in_x24): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0`, `out_x1`, `out_x10`, `out_x11`, `out_x12`, `out_x13`, `out_x14`,
  `out_x15`, `out_x16`, `out_x17`, `out_x18`, `out_x19`, `out_x2`, `out_x20`,
  `out_x21`, `out_x22`, `out_x23`, `out_x24`, `out_x3`, `out_x4`, `out_x5`, `out_x6`,
  `out_x7`, `out_x8`, `out_x9`: 64.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`keccak_f1600_round` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The 25 unsigned 64-bit inputs represent A[x,y]=in_x(5*y+x). Rotation is within 64 bits; bitwise complements are likewise 64-bit. One round computes:

```text
C[x] = XOR over y of A[x,y]
D[x] = C[x-1] XOR ROL64(C[x+1],1)
B[y,(2*x+3*y) mod 5] = ROL64(A[x,y] XOR D[x], r[x,y])
Anew[x,y] = B[x,y] XOR ((NOT B[x+1,y]) AND B[x+2,y])
Anew[0,0] = Anew[0,0] XOR RC[in_round_idx]
```

Coordinate subscripts are modulo 5. All right-hand sides of chi use B, not partially updated Anew. Rotation offsets r, listed by y then x, are:

```text
 0  1 62 28 27
36 44  6 55 20
 3 10 43 25 39
41 45 15 21  8
18  2 61 56 14
```

RC[0..23], in hexadecimal, is:

```text
0000000000000001 0000000000008082 800000000000808a 8000000080008000
000000000000808b 0000000080000001 8000000080008081 8000000000008009
000000000000008a 0000000000000088 0000000080008009 000000008000000a
000000008000808b 800000000000008b 8000000000008089 8000000000008003
8000000000008002 8000000000000080 000000000000800a 800000008000000a
8000000080008081 8000000000008080 0000000080000001 8000000080008008
```

For indices 24..31, RC is zero. Return all 25 lanes in the same order. A full permutation requires 24 such rounds; padding, absorption and squeezing are outside this task.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
