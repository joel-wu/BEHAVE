# HEVC SAO and Loop-Filter Pixel Primitives
Apply one SAO edge/band offset or one selected loop-filter pixel update to an 8-bit reconstructed sample. Neighbor samples and offset tables are supplied explicitly; frame traversal, neighbor caches, offset estimation, and boundary decisions are outside this arithmetic block.
## Interface
### RTL interface
```systemverilog
module signal_processing_video_loop_hevc_sao (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [2:0] in_mode,
    input logic [7:0] in_rec,in_a,in_b,in_c,in_f,
    input logic signed [7:0] in_offset0,in_offset1,in_offset2,in_offset3,in_offset4,
    input logic [255:0] in_band_offsets,
    input logic [7:0] in_tc_q,in_tc,
    input logic in_mask_q,
    output logic out_valid,
    input logic out_ready,
    output logic [7:0] out_rec,
    output logic signed [1:0] out_sign
);
endmodule
```
There are no parameters. All pixel inputs and thresholds are unsigned; the five edge offsets and the 32 packed band offsets are signed 8-bit integers. Band k occupies bits 8k+7:8k of `in_band_offsets`. `in_mask_q` enables the q-side chroma update.

An input/output transfers on a rising edge when its valid and ready are both high. Hold valid and all associated payload bits stable during backpressure. Each accepted input yields exactly one ordered output unless canceled by reset; no fixed latency is required.
### Behavior interface
`signal_processing_video_loop_hevc_sao()` provides `reset()` and
```python
process(in_mode, in_rec, in_a, in_b, in_c, in_f,
        in_offset0, in_offset1, in_offset2, in_offset3, in_offset4,
        in_band_offsets, in_tc_q, in_tc, in_mask_q)
```
Call `process` once per accepted input. It returns one dictionary
`{"out_rec": ..., "out_sign": ...}`. Both fields are Python integers:
`out_rec` holds an unsigned 8-bit value and `out_sign` a 2-bit port pattern.
## Modules
One stateless pixel-arithmetic block and its ordered ready/valid interface.
## Functional Behavior
Use exact signed integer arithmetic before the specified shifts and final clipping. Let `clip(x)=min(255,max(0,x))`, `clip_tc(x)=min(tc,max(-tc,x))`, and `sgn(x)` be -1, 0, or +1. Right shifts below are arithmetic.

| Mode | Operation |
| --- | --- |
| 0 | Return rec unchanged and sign(rec-a) |
| 1–4 | Edge offset: clip(rec + offset[sgn(rec-a)+sgn(rec-b)+2]) |
| 5 | Band offset: clip(rec + band_offset[rec >> 3]) |
| 6 | Strong-luma q0 update described below |
| 7 | Chroma q0 update described below |

For modes 1–4, a and b are the two **original, unfiltered** neighbor values in the selected direction. They are already chosen by the caller: horizontal, vertical, and the two diagonals use the same scalar formula. This interface does not maintain the source traversal's sign buffers. All five supplied edge-category offsets participate, including category 2. Mode 5 supports every one of the 32 bands independently.

In mode 6, map `(f,a,rec,b,c)` to `(m2,m3,m4,m5,m6)`. Compute
`prediction=(f+2*a+2*rec+2*b+c+4)>>3`,
then `out_rec=rec+clip_tc(prediction-rec)`, using `tc=in_tc_q`.
The convex average and clipping keep this result within [0,255].

In mode 7, map `(c,a,rec,b)` to `(m2,m3,m4,m5)`. Set
`delta=clip_tc((4*(rec-a)+c-b+4)>>3)`, using `tc=in_tc`.
If `in_mask_q=1`, return `clip(rec-delta)`; otherwise return rec.
Only the selected q0 pixel is returned; the corresponding p-side and other strong-filter outputs are not part of this task.

`out_sign` is zero except in mode 0, where -1 is encoded as 2'b11, zero as 00, and +1 as 01. Irrelevant inputs are ignored per mode. All bit patterns within the declared widths are defined.
## Reset Behavior
Reset is synchronous and active high. On a reset edge clear pending output and drive `out_valid=0`, `out_rec=0`, `out_sign=0`. Reset takes priority over acceptance. There is no retained numerical state.
