# Hamming Distance 64b Popcount

This module computes the Hamming distance between two 64-bit input words. The distance is the population count of the bitwise XOR of the inputs: each bit position contributes 1 if the two inputs differ at that position and 0 otherwise, and the 64 contributions are summed to produce an unsigned 7-bit result in the range 0 to 64.

## Interface

### RTL interface

```systemverilog
module data_systems_distance_hamming_dist_64b_popcount (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_bs1,
    input  logic [63:0] in_bs2,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [6:0]  out_distance
);
```

### Behavior interface

```python
class DataSystemsDistanceHammingDist64bPopcount:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_bs1, in_bs2): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_distance": ...}`, whose value is a Python integer holding the unsigned
7-bit Hamming distance. A zero distance still produces one record.

## Modules

- `data_systems_distance_hamming_dist_64b_popcount`: top-level module that accepts one pair of 64-bit words and produces their 7-bit Hamming distance.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

For each accepted input transaction, interpret `in_bs1` and `in_bs2` as unsigned 64-bit bit patterns.

1. Compute the 64-bit XOR word:
   - `diff = in_bs1 ^ in_bs2`

2. Compute the population count of `diff`:
   - `out_distance` equals the number of bit positions `i` in `[0, 63]` for which `diff[i] = 1`
   - equivalently, `out_distance = sum(((diff >> i) & 1) for i in 0..63)`

3. The output is encoded as an unsigned 7-bit integer.
   - Valid output range is `[0, 64]`
   - `out_distance = 0` when `in_bs1 == in_bs2`
   - `out_distance = 64` when `in_bs1` is the bitwise complement of `in_bs2`

Arithmetic is exact integer arithmetic. No signed interpretation, rounding, truncation, or saturation is applied beyond the 7-bit output encoding.

The mapping is 1:1: every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is asynchronous and active-high. On reset:
- `out_valid = 0`
- `out_distance = 0`

There is no architectural state other than any internal buffering required to implement the handshake. After reset, the module contains no queued input or output transaction.
