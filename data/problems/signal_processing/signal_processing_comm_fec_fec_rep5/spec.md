# FEC REP5

This block implements a 5-fold repetition forward-error-correction primitive over byte-oriented messages. It supports three transaction types: encode one decoded byte into five identical encoded bytes, hard-decode five encoded bytes into one decoded byte by bitwise majority vote across the five replicas, and soft-decode forty 8-bit soft metrics into one decoded byte by averaging the five metrics for each bit position and comparing the average against a fixed erasure threshold. The hard decoder uses the explicit 10-term 3-of-5 majority expression on each bit.

## Interface

### RTL interface

```systemverilog
module fec_rep5 (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [1:0] in_mode,
    input  logic [7:0] in_data0,
    input  logic [7:0] in_data1,
    input  logic [7:0] in_data2,
    input  logic [7:0] in_data3,
    input  logic [7:0] in_data4,
    input  logic [7:0] in_data5,
    input  logic [7:0] in_data6,
    input  logic [7:0] in_data7,
    input  logic [7:0] in_data8,
    input  logic [7:0] in_data9,
    input  logic [7:0] in_data10,
    input  logic [7:0] in_data11,
    input  logic [7:0] in_data12,
    input  logic [7:0] in_data13,
    input  logic [7:0] in_data14,
    input  logic [7:0] in_data15,
    input  logic [7:0] in_data16,
    input  logic [7:0] in_data17,
    input  logic [7:0] in_data18,
    input  logic [7:0] in_data19,
    input  logic [7:0] in_data20,
    input  logic [7:0] in_data21,
    input  logic [7:0] in_data22,
    input  logic [7:0] in_data23,
    input  logic [7:0] in_data24,
    input  logic [7:0] in_data25,
    input  logic [7:0] in_data26,
    input  logic [7:0] in_data27,
    input  logic [7:0] in_data28,
    input  logic [7:0] in_data29,
    input  logic [7:0] in_data30,
    input  logic [7:0] in_data31,
    input  logic [7:0] in_data32,
    input  logic [7:0] in_data33,
    input  logic [7:0] in_data34,
    input  logic [7:0] in_data35,
    input  logic [7:0] in_data36,
    input  logic [7:0] in_data37,
    input  logic [7:0] in_data38,
    input  logic [7:0] in_data39,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
```

`in_mode` encoding:
- `2'b00`: encode
- `2'b01`: hard decode
- `2'b10`: soft decode
- `2'b11`: encode

For encode mode, only `in_data0` is used and `in_data1` through `in_data39` are ignored.

For hard-decode mode, only `in_data0` through `in_data4` are used and `in_data5` through `in_data39` are ignored.

For soft-decode mode, all `in_data0` through `in_data39` are used.

### Behavior interface

```python
class FecRep5:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_mode,
        in_data0, in_data1, in_data2, in_data3, in_data4,
        in_data5, in_data6, in_data7, in_data8, in_data9,
        in_data10, in_data11, in_data12, in_data13, in_data14,
        in_data15, in_data16, in_data17, in_data18, in_data19,
        in_data20, in_data21, in_data22, in_data23, in_data24,
        in_data25, in_data26, in_data27, in_data28, in_data29,
        in_data30, in_data31, in_data32, in_data33, in_data34,
        in_data35, in_data36, in_data37, in_data38, in_data39,
    ):
        pass
```

`process()` returns:
- a list of 5 dictionaries in encode mode, each dictionary containing `{"out_data": <byte>}`
- one dictionary in hard-decode mode
- one dictionary in soft-decode mode
- a list of 5 dictionaries in mode `2'b11`, identical to encode mode

Modes 0 and 3 return a list of exactly five dictionaries in output order. Modes 1 and
2 return one dictionary. Never return None.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fec_rep5`: top-level repetition encoder/decoder block.

## Functional Behavior

All ports are unsigned integers. `in_data*` and `out_data` are 8-bit values. One accepted input transaction produces either 5 output transactions or 1 output transaction as defined below.

### Encode mode (`in_mode = 2'b00` or `2'b11`)

The block emits exactly 5 output transactions.

For output transactions 0 through 4, `out_data = in_data0`.

The 5 outputs are identical and ordered. If `in_data0 = 8'hA6`, the return value is:
- beat 0: `out_data = 8'hA6`
- beat 1: `out_data = 8'hA6`
- beat 2: `out_data = 8'hA6`
- beat 3: `out_data = 8'hA6`
- beat 4: `out_data = 8'hA6`

This is byte replication, not bit replication.

### Hard-decode mode (`in_mode = 2'b01`)

The block consumes 5 encoded bytes `s0` through `s4`:
- `s0 = in_data0`
- `s1 = in_data1`
- `s2 = in_data2`
- `s3 = in_data3`
- `s4 = in_data4`

The block emits exactly 1 output transaction with `out_data` equal to the bytewise majority vote across the five input bytes, computed bitwise by the explicit 3-of-5 sum-of-products expression:

```text
out_data =
    (s0 & s1 & s2) |
    (s0 & s1 & s3) |
    (s0 & s1 & s4) |
    (s0 & s2 & s3) |
    (s0 & s2 & s4) |
    (s0 & s3 & s4) |
    (s1 & s2 & s3) |
    (s1 & s2 & s4) |
    (s1 & s3 & s4) |
    (s2 & s3 & s4)
```

`&` and `|` are bitwise operators on 8-bit values. Each output bit is `1` iff at least 3 of the corresponding input bits across `s0..s4` are `1`.

Example: with `s0=8'h80`, `s1=8'h80`, `s2=8'h00`, `s3=8'h80`, `s4=8'h00`, the output is `8'h80` because bit 7 has three ones and all other bits have zero ones.

### Soft-decode mode (`in_mode = 2'b10`)

The block consumes 40 soft-metric bytes representing 5 repetitions of one decoded byte. The inputs are arranged as five groups of eight metrics:

- repetition 0, bit positions 0..7: `in_data0` through `in_data7`
- repetition 1, bit positions 0..7: `in_data8` through `in_data15`
- repetition 2, bit positions 0..7: `in_data16` through `in_data23`
- repetition 3, bit positions 0..7: `in_data24` through `in_data31`
- repetition 4, bit positions 0..7: `in_data32` through `in_data39`

For decoded output bit-loop index `j` where `j` ranges from 0 to 7:

- `m0 = in_data[j]`
- `m1 = in_data[8 + j]`
- `m2 = in_data[16 + j]`
- `m3 = in_data[24 + j]`
- `m4 = in_data[32 + j]`

where `in_data[k]` denotes `in_data0`..`in_data39` by numeric suffix.

Compute the unsigned integer sum:
- `sum = m0 + m1 + m2 + m3 + m4`

`sum` ranges from 0 to 1275.

Compute the integer average using exact integer division by 5 with the fractional part discarded:
- `avg = floor(sum / 5)`

The erasure threshold is the fixed unsigned 8-bit constant `0x7F` decimal 127.

If `avg > 127`, set output bit `(7 - j)` to `1`. Otherwise leave output bit `(7 - j)` at `0`.

The output byte is initialised to `8'h00` before processing any bit positions, and the decisions for `j=0..7` are ORed into that byte. Therefore:
- `j = 0` controls `out_data[7]`
- `j = 1` controls `out_data[6]`
- ...
- `j = 7` controls `out_data[0]`

This bit ordering matches `1 << (7 - j)` exactly.

Equivalent scalar equations are:
- `out_data[7] = 1` iff `floor((in_data0  + in_data8  + in_data16 + in_data24 + in_data32) / 5) > 127`
- `out_data[6] = 1` iff `floor((in_data1  + in_data9  + in_data17 + in_data25 + in_data33) / 5) > 127`
- `out_data[5] = 1` iff `floor((in_data2  + in_data10 + in_data18 + in_data26 + in_data34) / 5) > 127`
- `out_data[4] = 1` iff `floor((in_data3  + in_data11 + in_data19 + in_data27 + in_data35) / 5) > 127`
- `out_data[3] = 1` iff `floor((in_data4  + in_data12 + in_data20 + in_data28 + in_data36) / 5) > 127`
- `out_data[2] = 1` iff `floor((in_data5  + in_data13 + in_data21 + in_data29 + in_data37) / 5) > 127`
- `out_data[1] = 1` iff `floor((in_data6  + in_data14 + in_data22 + in_data30 + in_data38) / 5) > 127`
- `out_data[0] = 1` iff `floor((in_data7  + in_data15 + in_data23 + in_data31 + in_data39) / 5) > 127`

The block emits exactly 1 output transaction.

Example: if for `j=0` the five metrics are `255, 255, 255, 0, 0`, then `sum=765`, `avg=153`, `153 > 127`, so `out_data[7]=1`. If the five metrics are `255, 255, 0, 0, 0`, then `sum=510`, `avg=102`, `102 > 127` is false, so `out_data[7]=0`.

## Reset Behavior

The block uses synchronous active-high reset `rst`.

Architectural state: none.

After reset, there is no stored data and no buffered output. `out_valid` is low until an input transaction is accepted and an output transaction becomes available. `out_data` is `8'h00` whenever `out_valid` is low.
