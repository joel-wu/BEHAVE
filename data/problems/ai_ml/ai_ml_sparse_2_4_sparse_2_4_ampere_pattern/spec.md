# Magnitude-based 2:4 weight pruning

Retain the two largest-magnitude weights in each four-element group and return dense, mask and logical compact representations, not a backend-specific sparse-tensor encoding.

## Interface

### RTL interface

```systemverilog
module Sparse24Pattern (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [511:0] in_weights,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_mask,
    output logic [511:0] out_dense,
    output logic [255:0] out_values,
    output logic [31:0] out_indices
);
```

### Behavior interface

```python
class Sparse24Pattern:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_weights): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_dense`: 512.
- `out_indices`, `out_mask`: 32.
- `out_values`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

Sparse24Pattern is the complete fixed-shape operator. Packing is little-index-first as specified below.

## Functional Behavior

This task implements magnitude-based 2:4 pruning, corresponding to the source's `apply_fake_sparsity` configuration with sparsity_level=1, sparse_block_shape=(1,4), zeros_per_block=2. It does not implement backend-specific Ampere matrix swizzling or GPU instructions. The retained historical task ID is not a claim that the output is a hardware sparse-tensor ABI.

in_weights contains eight consecutive groups of four signed 16-bit weights: group g, column j is at [16*(4*g+j)+:16], interpreted as two's complement. Fixed-point scaling may be chosen uniformly; it does not affect ordering. Compare magnitudes exactly in 17 bits, so abs(-32768)=32768. Select the two largest magnitudes independently in each group; ties retain the smaller column index. This tie rule completes the source's unspecified equal-magnitude top-k behavior.

out_mask bit (4*g+j) is 1 exactly when that position is selected. out_dense has the original selected 16-bit words at their original positions, and zero elsewhere. The mask has exactly two set bits in each group, but the output can contain fewer than two nonzero values when selected weights are already zero.

Also return a logical compact representation. List the two selected columns in increasing column order, j0<j1. out_values slot 2*g+k, at [16*(2*g+k)+:16], contains that weight word. out_indices has the corresponding two-bit column at [2*(2*g+k)+:2]. This representation reconstructs out_dense exactly; it is not NVIDIA metadata encoding. No cross-group selection, padding or pruning-state update occurs.

One accepted input returns one mapping with all four output fields. A transfer occurs at a rising edge with valid and ready both high. Output valid and payload remain stable until acceptance. Input data takes effect only on acceptance; there is no cycle-count requirement or persistent numerical state.

## Reset Behavior

Synchronous active-high reset discards pending output and clears out_valid and all output payloads. in_ready=1 after reset. Payloads are zero when out_valid=0.
