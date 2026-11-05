# JPEG DC Predictor

This block implements the sequential JPEG DC prediction update for one decoded block. For each accepted input transaction, it receives a component index and the already-decoded DC difference for that block, adds the difference to the saved predictor for that component using 32-bit two's-complement wraparound arithmetic, stores the updated predictor back for that component, and emits the updated absolute DC coefficient value. The block also supports a restart event on the same input transaction interface; a restart clears all component predictors to zero before processing the block carried by that transaction.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_entropy_jpeg_dc_predictor #(
    parameter int MAX_COMPS = 4,
    parameter int COMP_IDX_W = 2
) (
    input  logic                    clk,
    input  logic                    rst,

    input  logic                    in_valid,
    output logic                    in_ready,
    input  logic                    in_restart,
    input  logic [COMP_IDX_W-1:0]   in_comp_idx,
    input  logic [31:0]             in_dc_diff,

    output logic                    out_valid,
    input  logic                    out_ready,
    output logic [31:0]             out_dc_value
);
```

`MAX_COMPS` is a positive number of DC predictor registers. `COMP_IDX_W` is positive and must satisfy `2^COMP_IDX_W >= MAX_COMPS`. With the defaults, `in_comp_idx` represents only indices 0 through 3.

`in_dc_diff` and `out_dc_value` carry signed 32-bit integers encoded as two's-complement bit patterns.

### Behavior interface

```python
class JpegDcPredictor:
    def __init__(self, max_comps=4):
        pass

    def reset(self):
        pass

    def process(self, in_restart, in_comp_idx, in_dc_diff):
        pass
```

`max_comps` matches the positive RTL parameter `MAX_COMPS`. Each call represents
one accepted block, including a block with `in_restart=1`, and returns exactly
`{"out_dc_value": dc_bits}`. Arguments use unsigned port encodings:
`in_restart` is 0 or 1, `in_comp_idx` fits `COMP_IDX_W`, and `in_dc_diff` and
`dc_bits` are 32-bit integers encoding signed values. A zero difference or
restart still returns the complete dictionary, never `None` or a list. No
handshake signals are returned. Component predictors persist between calls;
construction and `reset()` clear all predictors. `reset()` returns `None`
without processing a block, unlike a call carrying `in_restart=1`.

## Modules

- `compression_jpeg_entropy_jpeg_dc_predictor`: top-level DC predictor state machine and predictor register file.

## Functional Behavior

The block maintains an architectural state array `last_dc_val[0 .. MAX_COMPS-1]`. Each entry is a signed 32-bit predictor for one JPEG scan component.

Each accepted input transaction performs the following steps in order:

1. If `in_restart == 1`, then for every component index `i` in `0 .. MAX_COMPS-1`, set `last_dc_val[i] = 0`.
2. Form `ci_raw` from the input bit pattern on `in_comp_idx`.
3. Form the effective component index `ci = ci_raw mod MAX_COMPS`, using the unsigned value of the `COMP_IDX_W` input bits. For example, with `MAX_COMPS = 4` and `COMP_IDX_W = 3`, indices `0,1,2,3,4,5,6,7` map to `0,1,2,3,0,1,2,3`.
4. Interpret `in_dc_diff` as a signed 32-bit two's-complement integer `dc_diff`.
5. Read the old predictor `pred = last_dc_val[ci]`.
6. Compute `dc_value = pred + dc_diff` in signed 32-bit two's-complement wraparound arithmetic. This is modulo `2^32`, then reinterpreted as signed. No saturation is applied.
7. Write `last_dc_val[ci] = dc_value`.
8. Emit one output transaction with `out_dc_value` equal to the 32-bit two's-complement bit pattern of `dc_value`.

The block is 1:1: every accepted input produces exactly one output transaction.

Worked example with `MAX_COMPS = 4` and `COMP_IDX_W = 3`:
- Initial state after reset: all predictors are 0.
- Input `(in_restart=0, in_comp_idx=1, in_dc_diff=0x00000005)` produces `out_dc_value=0x00000005`, and predictor 1 becomes `+5`.
- Next input `(0, 1, 0xFFFFFFFD)` interprets `in_dc_diff` as `-3`, produces `out_dc_value=0x00000002`, and predictor 1 becomes `+2`.
- Input `(0, 5, 0x00000007)` uses effective component index `5 mod 4 = 1`.
- If predictor 1 is `0x7FFFFFFF` and the next `in_dc_diff` is `0x00000001`, the result wraps to `0x80000000`.

If `in_restart == 1` on the same transaction as a block decode, the predictor array is cleared first and then the current block is processed using the cleared predictor state. Therefore the emitted `out_dc_value` equals `in_dc_diff` interpreted as signed 32-bit, written back into the selected predictor entry.

## Reset Behavior

`rst` is active-high and synchronous. On reset:
- `last_dc_val[i] = 0` for every `i` in `0 .. MAX_COMPS-1`.
- `out_valid = 0`.
- `out_dc_value = 0`.
- `in_ready = 1`.

There is no other architectural state.
