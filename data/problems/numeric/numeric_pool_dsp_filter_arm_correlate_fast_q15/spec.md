# Q15CorrelationFast4

Q15CorrelationFast4 computes the fast Q15 correlation of two externally stored 4-sample sequences and writes the complete 7-sample result frame to an external destination memory. The transaction is a fixed-size specialization of the fast Q15 correlation algorithm with both sequence lengths equal to 4. Each output sample is formed by signed Q15 products accumulated in a wrapping 32-bit accumulator, then arithmetic-right-shifted by 15 bits and saturated to a signed 16-bit Q15 result.

## Interface

### RTL interface

```systemverilog
module q15_correlation_fast4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [10:0]  in_srcA_addr,
    input  logic [10:0]  in_srcB_addr,
    input  logic [10:0]  in_dst_addr,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_dst_len,

    output logic         data_req_valid,
    output logic         data_req_we,
    output logic [10:0]  data_req_addr,
    output logic [15:0]  data_req_wdata,
    input  logic         data_req_ready,
    input  logic         data_resp_valid,
    input  logic [15:0]  data_resp_rdata
);
```

`data` is a single read-write external memory port. The two input sequences each occupy 4 consecutive 16-bit words. The destination frame occupies 7 consecutive 16-bit words. Source and destination samples are signed Q15 values stored as 16-bit two's-complement integers.

### Behavior interface

```python
class Q15CorrelationFast4:
    def __init__(self, mem_depth=2048):
        pass

    def reset(self):
        pass

    def process(self, in_srcA_addr, in_srcB_addr, in_dst_addr):
        pass
```

`process(...)` returns one dict:
- `{"out_dst_len": ...}`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_dst_len`: 3.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

Use mem_depth=2048 for the published 11-bit address interface. self.data is a
read/write view of 2048 unsigned 16-bit words. The environment supplies its run image;
standalone construction uses zero-filled placeholders. Required destination writes are
side effects, not replaced by out_dst_len. Request pins are not return fields.

## Modules

- `q15_correlation_fast4`: top-level fixed-size correlation engine for one 4-sample by 4-sample frame.

## Functional Behavior

Let `A[0..3]` be the signed Q15 samples read from external memory addresses `in_srcA_addr + 0 .. in_srcA_addr + 3`. Let `B[0..3]` be the signed Q15 samples read from external memory addresses `in_srcB_addr + 0 .. in_srcB_addr + 3`. Each memory word is interpreted as a signed 16-bit two's-complement integer.

The output frame length is fixed:

- `dst_len = 7`

and the block returns `out_dst_len = 7` on every accepted transaction whose memory accesses are all in range.

The memory has 2048 words. If `in_srcA_addr > 2044`, `in_srcB_addr > 2044`, or `in_dst_addr > 2041`, the transaction performs no memory accesses and returns `out_dst_len = 0`. For a valid command, both four-word operands are read before the first write. The destination may overlap either source: results use those operand snapshots.

Because both input lengths are equal, no input swap occurs, no output reversal occurs, and no explicit zero-padding region exists. All 7 destination words are correlation samples.

Define logical outputs `r[0] .. r[6]` as follows.

### Stage 1: increasing overlap

- `acc0 = wrap32(A[0] * B[3])`
- `r[0] = sat_q15(arshift_32(acc0, 15))`

- `acc1 = wrap32((A[0] * B[2]) + (A[1] * B[3]))`
- `r[1] = sat_q15(arshift_32(acc1, 15))`

- `acc2 = wrap32(wrap32((A[0] * B[1]) + (A[1] * B[2])) + (A[2] * B[3]))`
- `r[2] = sat_q15(arshift_32(acc2, 15))`

### Stage 2: full overlap

- `acc3 = wrap32(wrap32(wrap32((A[0] * B[0]) + (A[1] * B[1])) + (A[2] * B[2])) + (A[3] * B[3]))`
- `r[3] = sat_q15(arshift_32(acc3, 15))`

### Stage 3: decreasing overlap

- `acc4 = wrap32(wrap32((A[1] * B[0]) + (A[2] * B[1])) + (A[3] * B[2]))`
- `r[4] = sat_q15(arshift_32(acc4, 15))`

- `acc5 = wrap32((A[2] * B[0]) + (A[3] * B[1]))`
- `r[5] = sat_q15(arshift_32(acc5, 15))`

- `acc6 = wrap32(A[3] * B[0])`
- `r[6] = sat_q15(arshift_32(acc6, 15))`

### Arithmetic details

All source samples are signed 16-bit integers representing Q15 values.

For each product:
- interpret operands as signed 16-bit integers
- compute the exact signed integer product

`wrap32(x)` means:
1. retain only the low 32 bits of `x`
2. reinterpret that 32-bit pattern as a signed two's-complement 32-bit integer

`arshift_32(x, 15)` means arithmetic right shift by 15 of the signed 32-bit integer `x`.

`sat_q15(x)` means:
- if `x > 32767`, return `32767`
- else if `x < -32768`, return `-32768`
- else return `x`

For final conversion of one accumulator to one destination sample:
1. arithmetic-right-shift the signed 32-bit accumulator by 15 bits
2. clamp the shifted value to the signed 16-bit range `[-32768, 32767]`
3. write the clamped result to memory as a 16-bit two's-complement word

### External memory side effects

For each accepted input transaction with all accesses in range, the block shall:
1. read 4 words starting at `in_srcA_addr`
2. read 4 words starting at `in_srcB_addr`
3. write 7 words starting at `in_dst_addr`

The writes are:
- `data[in_dst_addr + 0] = r[0]`
- `data[in_dst_addr + 1] = r[1]`
- `data[in_dst_addr + 2] = r[2]`
- `data[in_dst_addr + 3] = r[3]`
- `data[in_dst_addr + 4] = r[4]`
- `data[in_dst_addr + 5] = r[5]`
- `data[in_dst_addr + 6] = r[6]`

Each written word is the 16-bit two's-complement encoding of the corresponding signed Q15 result. The completion output becomes valid only after all seven writes are accepted. Writes from completed commands are visible to subsequent commands. While a request is stalled, its valid bit, address, write-enable and write data remain stable; output valid and length similarly remain stable until accepted. Read responses arrive no earlier than the cycle after request acceptance; their latency may vary.

### Edge cases

- Any out-of-range source read or destination write: no memory writes, `out_dst_len = 0`
- Any accumulator overflow during summation wraps modulo `2^32` in two's-complement form
- Final output saturation is applied after the right shift on every sample
- There are no special non-numeric cases because arithmetic is purely signed integer fixed-point

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `in_ready` shall be `1`
- `out_valid` shall be `0`
- `out_dst_len` shall be `0`
- `data_req_valid` shall be `0`
- `data_req_we` shall be `0`
- `data_req_addr` shall be `0`
- `data_req_wdata` shall be `0`

Architectural state after reset:
- no transaction is in flight
- no buffered source data is retained
- no partial accumulator value is retained
- no pending output length is retained
- no pending memory request is retained

Reset cancels the in-flight command and its unaccepted requests; it does not
issue writes to clear or roll back external memory. Writes already accepted
belong to the external memory. The test environment owns memory initialization
and pending-response cancellation across reset.
