# Curve25519 differential ladder step

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module cryptography_pk_classic_curve25519_ladder_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x2,
    input  logic [255:0] in_z2,
    input  logic [255:0] in_x3,
    input  logic [255:0] in_z3,
    input  logic [255:0] in_xp,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_x2,
    output logic [255:0] out_z2,
    output logic [255:0] out_x3,
    output logic [255:0] out_z3
);
```

### Behavior interface

```python
class Curve25519LadderStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x2, in_z2, in_x3, in_z3, in_xp): ...
```

Call `process()` once per accepted input with five unsigned 256-bit Python
integers. It returns one dictionary with exactly `out_x2`, `out_z2`, `out_x3`,
and `out_z3`. Each value is a Python integer in `[0, p)`, encoded on its 256-bit
port. Zero coordinates are ordinary output values, not absent records. No
calls represent stalls or latency. `reset()` returns `None`; there is no
ladder state shared between calls.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high.
Outputs transfer when out_valid and out_ready are both high. A stalled producer
holds valid and payload stable until transfer. Output order follows accepted
inputs. Each accepted input produces exactly one output transaction.

## Modules

`cryptography_pk_classic_curve25519_ladder_step` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

Compute one differential Montgomery-ladder step over the field p = 2^255-19. This is a field-arithmetic kernel, not a complete X25519 operation; it does not validate points or perform scalar clamping.

Normalize all five unsigned 256-bit inputs modulo p. Set A=x2+z2, B=x2-z2, C=x3+z3, D=x3-z3, AA=A², BB=B², E=AA-BB. The four outputs, each reduced to [0,p), are:

- out_x2 = AA*BB;
- out_z2 = E*(AA+121665*E);
- out_x3 = (D*A+C*B)²;
- out_z3 = xp*(D*A-C*B)².

All arithmetic in these equations is modulo p. Inputs are raw encodings, not necessarily canonical field elements.

## Reset Behavior

Reset rst is synchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. Output payload registers reset to zero.
