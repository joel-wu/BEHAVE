# Sixteen-bit XNOR LFSR selector

A source-derived finite hardware kernel with explicit integer and transaction semantics.

## Interface

### RTL interface

```systemverilog
module lfsr_way_selector #(
    parameter logic [15:0] SEED  = 16'h0000,
    parameter int unsigned WIDTH = 16
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic                     in_en,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [WIDTH-1:0]         out_refill_way_oh,
    output logic [$clog2(WIDTH)-1:0] out_refill_way_bin
);
```

### Behavior interface

```python
class LfsrWaySelector:
    def __init__(self, seed=0, width=16): ...
    def reset(self): ...
    def process(self, in_en): ...
```

Call process once per accepted input, not once per latency cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_refill_way_bin`: ceil(log2(WIDTH)).
- `out_refill_way_oh`: WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor seed and width match RTL SEED and WIDTH.

### Transaction protocol

Inputs are accepted on a rising edge with in_valid and in_ready both high. Outputs transfer when out_valid and out_ready are both high. A stalled producer holds valid and payload stable until transfer. Output order follows accepted inputs. Each accepted input produces exactly one output transaction.

## Modules

`lfsr_way_selector` is the top-level module. Internal decomposition and pipeline structure are implementation choices.

## Functional Behavior

The persistent 16-bit state starts at SEED (default zero). WIDTH is a parameter in [2,16], default 16. On each accepted input, let next_bit=1 XOR state[15] XOR state[12] XOR state[5] XOR state[1], then state=((state<<1)|next_bit) mod 2^16. The input payload in_en controls whether this step is enabled: when zero, retain the state. Return the low ceil(log2(WIDTH)) state bits as the binary selector, and the WIDTH-bit one-hot value 1<<selector if selector<WIDTH, otherwise zero.

The returned selector is from the updated state. Non-power-of-two WIDTH may therefore emit a zero one-hot result; an all-ones seed is an allowed absorbing state. This is a deterministic source-derived selector, not a claim of cryptographic randomness.

## Reset Behavior

Reset rst is asynchronous active-high. It discards pending outputs and in-flight work; out_valid becomes zero. No pre-reset transaction is emitted after reset. The LFSR returns to SEED; the reset binary and one-hot outputs encode that seed using the same selector rule.
