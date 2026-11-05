# fixed_point_adder

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule #(parameter int Q = 15, parameter int N = 32) (
    input wire [N-1:0] a,
    input wire [N-1:0] b,
    output wire [N-1:0] c
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, Q=15, N=32): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Pass `a` and `b` as unsigned N-bit sign-magnitude encodings, not signed Python
numbers. Every call returns exactly one dictionary `{"c": value}`, with an
unsigned N-bit encoding, including for positive or negative zero. Never return
`None` or a list. Calls retain no state. Construction selects Q and N;
`reset()` preserves these parameters, has no other effect and returns `None`.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Sign-magnitude encoding: bit N-1 is sign, low N-1 bits are magnitude, with Q fractional bits. Require N>=2 and 0<=Q<=N-1; defaults N=32,Q=15. Same signs: add magnitudes modulo 2^(N-1), retaining the common sign, including negative zero. Opposite signs: subtract smaller magnitude from larger and retain the larger operand's sign; equal magnitudes produce positive zero. No rounding, rescaling, saturation or overflow flag. Q changes interpretation only.

## Reset Behavior

There is no RTL reset or persistent state.
