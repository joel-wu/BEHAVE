# signal_processing_comm_fec_fec_rep3

This block implements a repetition-3 forward-error-correction codec at the transaction level. It supports three operations selected by `in_mode`: byte replication encode, hard-decision majority decode of three replicated bytes, and soft-decision decode of one original byte from 24 soft-bit metrics. Each accepted input transaction produces exactly one output transaction. Encode replicates one 8-bit input byte into three identical 8-bit output bytes. Hard decode computes the bitwise majority of three 8-bit input bytes. Soft decode groups 24 unsigned 8-bit soft metrics as three replicas of 8 bit positions, averages each bit position's three metrics with integer division by 3, and decides output bit 1 when the average is greater than 127, else 0.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_rep3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_mode,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,
    input  logic [7:0]  in_data2,
    input  logic [7:0]  in_data3,
    input  logic [7:0]  in_data4,
    input  logic [7:0]  in_data5,
    input  logic [7:0]  in_data6,
    input  logic [7:0]  in_data7,
    input  logic [7:0]  in_data8,
    input  logic [7:0]  in_data9,
    input  logic [7:0]  in_data10,
    input  logic [7:0]  in_data11,
    input  logic [7:0]  in_data12,
    input  logic [7:0]  in_data13,
    input  logic [7:0]  in_data14,
    input  logic [7:0]  in_data15,
    input  logic [7:0]  in_data16,
    input  logic [7:0]  in_data17,
    input  logic [7:0]  in_data18,
    input  logic [7:0]  in_data19,
    input  logic [7:0]  in_data20,
    input  logic [7:0]  in_data21,
    input  logic [7:0]  in_data22,
    input  logic [7:0]  in_data23,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_data0,
    output logic [7:0]  out_data1,
    output logic [7:0]  out_data2
);
```

### Behavior interface

```python
class signal_processing_comm_fec_fec_rep3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_mode,
            in_data0, in_data1, in_data2, in_data3, in_data4, in_data5,
            in_data6, in_data7, in_data8, in_data9, in_data10, in_data11,
            in_data12, in_data13, in_data14, in_data15, in_data16, in_data17,
            in_data18, in_data19, in_data20, in_data21, in_data22, in_data23):
        pass
```

The return value of `process(...)` is always one dict with keys `out_data0`, `out_data1`, and `out_data2`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data0`, `out_data1`, `out_data2`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_fec_fec_rep3`: top-level repetition-3 FEC codec.

## Functional Behavior

All `in_data*` and `out_data*` ports are unsigned 8-bit values.

One accepted input transaction produces exactly one output transaction.

`in_mode` selects the operation:

- `in_mode == 2'b00`: encode.
- `in_mode == 2'b01`: hard-decision decode.
- `in_mode == 2'b10`: soft-decision decode.
- `in_mode == 2'b11`: encode.

For `in_mode` equal to encode (`2'b00` or `2'b11`):
- Only `in_data0` is used.
- `out_data0 = in_data0`.
- `out_data1 = in_data0`.
- `out_data2 = in_data0`.
- All other input bytes are ignored.

For `in_mode == 2'b01` hard-decision decode:
- Only `in_data0`, `in_data1`, and `in_data2` are used.
- The output is the bitwise majority vote of the three input bytes.
- For each bit position `k` from 0 to 7 independently,
  `out_data0[k] = (in_data0[k] & in_data1[k]) | (in_data0[k] & in_data2[k]) | (in_data1[k] & in_data2[k])`.
- `out_data1 = 8'h00`.
- `out_data2 = 8'h00`.
- All other input bytes are ignored.

Worked example for hard decode:
- If `in_data0 = 8'hA5`, `in_data1 = 8'hA1`, `in_data2 = 8'hE5`,
  then `out_data0 = (8'hA5 & 8'hA1) | (8'hA5 & 8'hE5) | (8'hA1 & 8'hE5) = 8'hA5`,
  and `out_data1 = 8'h00`, `out_data2 = 8'h00`.

For `in_mode == 2'b10` soft-decision decode:
- `in_data0` through `in_data23` are used.
- These 24 bytes represent soft metrics for one decoded output byte, arranged as three repeated groups of eight metrics.
- For output bit 7, use `in_data0`, `in_data8`, `in_data16`.
- For output bit 6, use `in_data1`, `in_data9`, `in_data17`.
- For output bit 5, use `in_data2`, `in_data10`, `in_data18`.
- For output bit 4, use `in_data3`, `in_data11`, `in_data19`.
- For output bit 3, use `in_data4`, `in_data12`, `in_data20`.
- For output bit 2, use `in_data5`, `in_data13`, `in_data21`.
- For output bit 1, use `in_data6`, `in_data14`, `in_data22`.
- For output bit 0, use `in_data7`, `in_data15`, `in_data23`.
- For each output bit, form an unsigned 10-bit arithmetic sum of the three 8-bit metrics, compute `avg = sum / 3` using integer division truncating toward zero, and set the decoded bit to 1 iff `avg > 127`; otherwise set it to 0.
- `out_data0` is the assembled decoded byte with the bit mapping listed above.
- `out_data1 = 8'h00`.
- `out_data2 = 8'h00`.

Worked example for one soft-decision bit:
- If the three metrics for a bit are 120, 128, and 140, then `sum = 388`, `avg = 388 / 3 = 129`, and the decoded bit is 1 because `129 > 127`.
- If the three metrics are 127, 127, and 127, then `sum = 381`, `avg = 127`, and the decoded bit is 0 because the comparison is strictly greater than 127.

No saturation occurs. All output values are exact 8-bit results of the operations above.

## Reset Behavior

The block uses synchronous active-high reset `rst`.

On reset:
- `out_valid = 1'b0`
- `out_data0 = 8'h00`
- `out_data1 = 8'h00`
- `out_data2 = 8'h00`

The block has no other architectural state.
