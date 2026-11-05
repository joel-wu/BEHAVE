# CorrelateFastOptQ15SingleOutput

Compute one selected sample of the full Q15 cross-correlation between two
external sequences. The kernel retains fast-Q15 arithmetic: exact products,
32-bit wrapping accumulation, an arithmetic shift, and final saturation.
The destination index includes leading or trailing padding when lengths differ.

## Interface

### RTL interface

```systemverilog
module correlate_fast_opt_q15_single_output (
    input  logic clk, rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [7:0] in_len_a, in_len_b, in_a_base, in_b_base,
    input  logic [8:0] in_out_index,
    output logic out_valid,
    input  logic out_ready,
    output logic [15:0] out_sample,
    output logic out_nonzero_region,
    output logic a_req_valid,
    output logic [7:0] a_req_addr,
    input  logic a_req_ready, a_resp_valid,
    input  logic [15:0] a_resp_rdata,
    output logic b_req_valid,
    output logic [7:0] b_req_addr,
    input  logic b_req_ready, b_resp_valid,
    input  logic [15:0] b_resp_rdata
);
endmodule
```

### Behavior interface

```python
class CorrelateFastOptQ15SingleOutput:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_len_a, in_len_b, in_a_base, in_b_base, in_out_index): ...
```

The returned dictionary has keys `out_sample` and `out_nonzero_region`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_nonzero_region`: 1.
- `out_sample`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The memory views self.a and self.b each contain 256 unsigned 16-bit words. The
environment supplies the run images; standalone construction uses zero-filled
placeholders. Reset does not require clearing external memory. Request pins are not
return fields.

## Modules

- `correlate_fast_opt_q15_single_output`: one-command/one-result correlation kernel.
- `a` and `b`: independent external read-only memories of 256 raw Q15 words each.

## Functional Behavior

Lengths `NA=in_len_a` and `NB=in_len_b` are unsigned integers in `[0,255]`.
Base addresses are unsigned 8-bit values; all effective addresses wrap modulo
256. The unsigned 9-bit index `k=in_out_index` can select every position of
the largest destination (509 samples), as well as out-of-range values.

If either length is zero, return `(out_sample,out_nonzero_region)=(0,0)`.
Otherwise let `L=max(NA,NB)` and `D=2*L-1`. If `k>=D`, return `(0,0)`.
For an in-range destination, its physical lag is `lag=k-(L-1)`. Read

```text
A[i] = signed16(a[(in_a_base+i) mod 256]), 0<=i<NA
B[j] = signed16(b[(in_b_base+j) mod 256]), 0<=j<NB
acc = wrap32(sum(A[i]*B[j] for i-j=lag, 0<=i<NA, 0<=j<NB))
out_sample = sat16(acc >> 15)
```

`signed16` interprets the raw word as two's-complement. Products are exact;
`wrap32` keeps the low 32 bits and interprets them as signed. No intermediate
saturation is applied. The right shift rounds toward negative infinity, and
`sat16(v)=min(32767,max(-32768,v))`. Return the result as a 16-bit pattern.
Wrapping after each addition or once after the sum is equivalent.

`out_nonzero_region` indicates an overlapping correlation position, **not**
that the numerical result is nonzero. It is one exactly when at least one
pair `(i,j)` meets the bounds above. For `NA>=NB`, indices below `NA-NB` are
leading padding; for `NA<NB`, indices at or above `NA+NB-1` are trailing
padding. Padding returns `(0,0)` without requiring sample reads.

Each memory read request transfers when its `req_valid && req_ready` are high
on a rising edge. Hold request valid and address until that edge. The memory
then supplies one response with `resp_valid`; responses are in request order
per port. These read-only interfaces expose no write-enable or write-data pins.
Memory images are supplied by the environment, remain fixed during a command,
and may contain arbitrary nonzero words. The kernel must not substitute a
private zero-filled array. Read order and internal parallelism are not fixed.

One rising-edge `in_valid && in_ready` accepts a command and yields one ordered
output. Hold input valid and payload until acceptance, and hold output valid
and both output fields while `out_ready` is low. There is no fixed latency.

## Reset Behavior

Reset is synchronous active-high. It clears output valid and both result
fields, withdraws memory requests, discards pending work, and makes the input
available. The environment also discards pre-reset pending read responses;
no pre-reset result may appear afterward. Reset does not zero external data.
No numerical history persists between commands.
