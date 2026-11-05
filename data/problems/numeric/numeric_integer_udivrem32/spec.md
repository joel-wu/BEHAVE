# UDivRem32

Compute the exact quotient and remainder of unsigned 32-bit integer division.
All input bit patterns, including a zero divisor, have a defined result.

## Interface

### RTL interface

```systemverilog
module UDivRem32 (
    input  logic        clk, rst, in_valid, out_ready,
    output logic        in_ready, out_valid,
    input  logic [31:0] in_n, in_d,
    output logic [63:0] out_values
);
endmodule
```

Bits 31:0 contain the quotient and bits
63:32 contain the remainder, both unsigned.

### Behavior interface

```python
class UDivRem32:
    def reset(self): ...
    def process(self, in_n, in_d): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_values": packed_result}`, including for a zero divisor. Arguments and
`packed_result` are Python integers holding unsigned port encodings; the result
is packed, not a pair of separate values.

Behavior `reset()` has no numerical
state to clear.

### Transaction protocol

Outside reset, input and output transfers occur on rising edges with
`in_valid && in_ready` and `out_valid && out_ready`, respectively. Every
accepted input produces exactly one output in acceptance order. Producers
hold valid and payload stable while stalled. Bubbles do not create work;
backpressure must not drop or duplicate results. Internal latency and
throughput are not prescribed; iterative and pipelined implementations
are both permitted.

## Modules

`UDivRem32` is the only required public module.

## Functional Behavior

For `in_d > 0`, return the unique integers `q,r` satisfying
`in_n = q*in_d + r` and `0 <= r < in_d`. Thus `q` is the floor of the
unsigned quotient, not a signed division or floating-point approximation.
Both results fit in 32 bits. Intermediate arithmetic must be exact.

For `in_d == 0`, return `q = 0xFFFFFFFF` and `r = in_n`. This explicit
totalization replaces an exception or undefined hardware behavior.

Pack `out_values = (r << 32) | q`. For example:

- `in_n=100, in_d=7` gives `0x000000020000000E`.
- `in_n=0xFFFFFFFF, in_d=1` gives `0x00000000FFFFFFFF`.
- `in_n=5, in_d=0` gives `0x00000005FFFFFFFF`.

The mathematical operation has no state between requests.

## Reset Behavior

`rst` is synchronous and active high. It overrides transfers on its sampled
edge, cancels pending work, and clears `out_valid` and `out_values`. No
pre-reset result may appear after reset.
