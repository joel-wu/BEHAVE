# BPEMerge8

Complete bounded byte-pair merge loop on eight token slots and eight ranked merge rules.

## Interface

### RTL interface

```systemverilog
module BPEMerge8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [95:0] in_rules,
    input logic [7:0] in_rule_mask,
    output logic [38:0] out_values
);
endmodule
```

### Behavior interface

```python
class BPEMerge8:
    def reset(self): ...
    def process(self, in_tokens, in_length, in_rules, in_rule_mask): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 39.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Any unused output slots are zero as defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`BPEMerge8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Complete bounded byte-pair merge loop on eight token slots and eight ranked merge rules.

L=min(8,in_length); tokens are unsigned four-bit vocabulary IDs. Rule r is a 12-bit word left | (right<<4) | (replacement<<8), active iff its mask bit is set. Lower r means higher priority. Repeatedly select an adjacent matching pair by smallest rule index, then leftmost position; replace it and compact the sequence. Stop when no rule matches or only one token remains (at most seven merges). Duplicate pair definitions use the first active rule. Return up to eight four-bit tokens, zero padded, Lout at bits 32..35 and number of merges at 36..38. No dropout, byte pretokenization or text normalization is included. This is the complete Word.merge_all core with deterministic rank/position ordering.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Tokens [1,2,1,2] and rules rank0 (1,2)->3, rank1 (3,3)->4 yield [4], length 1, three merges.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
