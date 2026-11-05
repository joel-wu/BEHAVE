# Kyber signed Montgomery reduction

Interpret in_a as signed 32-bit a.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_kyber_kyber_montgomery_reduce (
    input  logic               clk,
    input  logic               rst,
    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_a,
    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [15:0] out_t
);
```

### Behavior interface

```python
class KyberMontgomeryReduce:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a): ...
```

The `process()` method returns a dict with key `out_t`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_t`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Behavior

Inputs transfer on rising clock edges with in_valid and in_ready both high, outside reset. The sender holds a pending input and its payload until transfer or reset. Outputs transfer when out_valid and out_ready are both high; the DUT must hold a stalled valid output and all of its fields unchanged. Output cardinality is one record per accepted request. Results follow accepted-input order. Latency and internal architecture are not fixed.

## Modules

`cryptography_pqc_kyber_kyber_montgomery_reduce` implements the public interface and operation specified here.

## Functional Behavior

Interpret in_a as signed 32-bit a. Let s16 and s32 mean two's-complement reinterpretation of the low 16 or 32 bits. Compute u=s16(a*62209), m=s32(a-u*3329), and return (m >> 16) encoded in 16 bits, with arithmetic right shift.

62209 is +3329^(-1) modulo 2^16, not its negative; the reduction subtracts u*q. In the source domain -3329*2^15 <= a < 3329*2^15, no 32-bit subtraction overflow occurs, the result is in [-3328,3328], and it is congruent to a*2^(-16) modulo 3329. Outside that domain the explicit s32 wrap defines a hardware extension; it does not assign semantics to C signed overflow. There is no final canonicalization or saturation.

## Reset Behavior

Reset is active-high and synchronous. Reset clears all pending output transactions; none may reappear after release. At a reset clock edge out_valid is zero, output payload registers are zero and the unit is ready for a fresh request. Algorithmic state does not persist across requests; any stream expansion is discarded by reset.
