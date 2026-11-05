# fixed_point_substractor

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

Construct `Behavior(Q=15, N=32)` with the same parameters as the RTL instance.
Each `process` call supplies unsigned N-bit integers `a` and `b` and returns
exactly one dictionary `{"c": value}`, where `value` is an unsigned N-bit
bit pattern with the sign-magnitude interpretation below. Zero is still a
dictionary result, never `None` or a list. Calls have no persistent state.
Construction produces no output record; `reset()` has no effect, returns
`None` and produces no output record.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Sign-magnitude encoding: bit N-1 is sign, low N-1 bits are magnitude, with Q fractional bits. Require N>=2 and 0<=Q<=N-1; defaults N=32,Q=15. Compute the mathematical signed difference a-b, then keep its sign and truncate its absolute magnitude modulo 2^(N-1). If the resulting magnitude is zero, force positive zero. Equivalently, different signs add magnitudes and use a's sign; same signs subtract magnitudes and reverse a's sign if b has greater magnitude. This corrects the original inconsistent sign rules: +1-(+2)=-1 and +1-(-2)=+3. No saturation or rounding; Q changes interpretation only.

## Reset Behavior

There is no RTL reset or persistent state.
