# Dilithium MakeHint

This block computes the Dilithium/ML-DSA hint bit `h` from signed low part `a0` and unsigned high part `a1`. The function is the `make_hint` predicate used by the rounding subsystem of the scheme: it reports whether the low part `a0` would overflow into the high part `a1` under the decomposition rules for the ML-DSA parameter set 44. The computation is purely combinational at the transaction level and produces one 1-bit result per accepted input.

## Interface

### RTL interface

```systemverilog
module cryptography_pqc_dilithium_dilithium_makehint (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_a0,
    input  logic [5:0]  in_a1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [0:0]  out_hint
);
```

`in_a0` carries a signed 24-bit two's-complement integer.
`in_a1` carries an unsigned 6-bit integer.

### Behavior interface

```python
class DilithiumMakeHint:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a0, in_a1):
        pass
```

Each call represents one accepted input and returns exactly
`{"out_hint": value}`, with `value` integer 0 or 1. Arguments are unsigned
24-bit and 6-bit port encodings; signed `a0` uses two's complement.
The return is never `None` or a list. The model is stateless; `reset()`
returns `None` without emitting a record.

## Modules

- `cryptography_pqc_dilithium_dilithium_makehint`: top-level module implementing the hint predicate for ML-DSA parameter set 44.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

The block implements the ML-DSA parameter set 44 `make_hint` predicate with:

- `MLDSA_GAMMA2 = 95232`

Inputs are interpreted as:

- `a0 = in_a0` as a signed 24-bit two's-complement integer, so its mathematical range is `[-2^23, 2^23 - 1]`.
- `a1 = in_a1` as an unsigned 6-bit integer, so its mathematical range is `[0, 63]`.

For each accepted input transaction, compute `out_hint` as follows:

1. If `a0 > 95232`, then `out_hint = 1`.
2. Else if `a0 < -95232`, then `out_hint = 1`.
3. Else if `a0 == -95232` and `a1 != 0`, then `out_hint = 1`.
4. Else `out_hint = 0`.

`out_hint` is a 1-bit unsigned output and must be driven as `0` or `1`.

No other input restrictions apply. In particular, the block does not check whether `(a0, a1)` came from a valid decomposition; it applies the predicate exactly as written to all 24-bit / 6-bit input bit patterns.

## Reset Behavior

The block has no architectural state.

Reset is active-high `rst`. Reset may be implemented synchronously or asynchronously. While in reset, the block shall not retain any transaction-dependent state.

After reset, the block behaves identically to a stateless combinational 1:1 mapper. The architectural output value associated with any accepted input is determined only by that input and the Functional Behavior section. If the implementation registers outputs, `out_valid` and `out_hint` shall be cleared to `0` in reset. `in_ready` shall be `1` whenever the block can accept an input transaction; for a stateless implementation it may be tied high.
