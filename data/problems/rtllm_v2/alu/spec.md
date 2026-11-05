# alu

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
    input wire [31:0] a,
    input wire [31:0] b,
    input wire [5:0] aluc,
    output wire [31:0] r,
    output wire zero,
    output wire carry,
    output wire negative,
    output wire overflow,
    output wire flag
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, aluc): ...
```

Each call returns exactly one dictionary with keys `r`, `zero`, `carry`,
`negative`, `overflow` and `flag`. `a`, `b` and `r` are unsigned 32-bit port
encodings, `aluc` is an unsigned 6-bit integer, and every flag is 0 or 1.
Signed operations interpret these words as specified below. Unsupported
opcodes still return every field, including `r=0` and `zero=1`, never `None`
or a list. There is no call-to-call state. Construction and `reset()` leave
this stateless mapping unchanged; `reset()` returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs; there is
no clock or handshake.

A 32-bit MIPS-style combinational ALU, not a RISC-V instruction decoder. All arithmetic wraps modulo 2^32. Signed comparisons and arithmetic shifts use two's complement.

| aluc (hex) | Operation | Result r |
| --- | --- | --- |
| 20 / 21 | ADD / ADDU | a+b |
| 22 / 23 | SUB / SUBU | a-b |
| 24 / 25 / 26 / 27 | AND / OR / XOR / NOR | bitwise AND / OR / XOR / NOR of a and b |
| 2A / 2B | SLT / SLTU | signed / unsigned a<b, zero-extended |
| 00 / 02 / 03 | SLL / SRL / SRA | b shifted by the FULL unsigned value a |
| 04 / 06 / 07 | SLLV / SRLV / SRAV | b shifted by a[4:0] |
| 0F | LUI | {a[15:0],16'b0} |
| other | Unsupported | 0 |

Left shifts and logical right shifts by 32 or more yield zero; arithmetic right shifts by 32 or more yield all sign bits. Fixed shifts do not mask a to five bits. LUI uses the low half of a.

For every opcode, zero=(r==0) and negative=r[31]. Carry is unsigned addition carry for ADD/ADDU, unsigned no-borrow (a>=b) for SUB/SUBU, and zero otherwise. Overflow is signed two's-complement overflow for ADD/SUB only and zero otherwise. Flag is the comparison result for SLT/SLTU and zero otherwise. These are explicit binary completions of the source's undriven flags and high-impedance cases. Zero tests the actual 32-bit result, correcting the source's wider intermediate test. No flags or outputs are X/Z.

## Reset Behavior

There is no RTL reset or persistent state.
