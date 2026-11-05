# Q15CorrelationStream

Compute the discrete cross-correlation of two external Q15 sequences of one
to eight samples. Each command emits all overlapping lags in increasing
order, without extra zero-padding slots. Products are accumulated exactly,
arithmetically shifted by 15 and saturated to Q15.

## Interface

### RTL interface

```systemverilog
module q15_correlation_stream (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_srcA_base,
    input  logic [2:0]   in_srcA_len_m1,
    input  logic [7:0]   in_srcB_base,
    input  logic [2:0]   in_srcB_len_m1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_data,

    output logic         sample_req_valid,
    output logic [7:0]   sample_req_addr,
    input  logic         sample_req_ready,
    input  logic         sample_resp_valid,
    input  logic [15:0]  sample_resp_rdata
);
```

`sample` is a read-only external memory of 256 16-bit words. A read request
transfers when `sample_req_valid && sample_req_ready`; valid and address must
remain stable while waiting. One response arrives for each accepted read,
no earlier than the following cycle; read latency may vary. Reads do not
modify memory. Effective addresses wrap modulo 256.

Input and output transactions use ready/valid. An offered payload remains
stable until accepted. Commands and their result frames are ordered and are
not interleaved. No fixed pipeline or execution latency is required.

### Behavior interface

```python
class Q15CorrelationStream:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_srcA_base, in_srcA_len_m1, in_srcB_base, in_srcB_len_m1): ...
```

`process` returns an ordered list of `{"out_data": value}` dictionaries.
Its `sample` mirror contains the same external memory image used by RTL.

Return a list of exactly na+nb-1 dictionaries, where na=in_srcA_len_m1+1 and
nb=in_srcB_len_m1+1. Entries follow increasing correlation index k=0 through na+nb-2.
Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The memory view self.sample contains 256 unsigned 16-bit words. The environment
supplies the run image; standalone construction uses a zero-filled placeholder. Reset
does not require clearing external memory. Request pins are not return fields.

## Modules

- `q15_correlation_stream`: correlation kernel and ordered streaming interface.
- `sample`: environment-owned, read-only sample memory.

## Functional Behavior

Let `na = in_srcA_len_m1 + 1` and `nb = in_srcB_len_m1 + 1`.
Read signed 16-bit two's-complement samples
`A[i] = sample[(in_srcA_base+i) mod 256]`, for `0 <= i < na`, and
`B[j] = sample[(in_srcB_base+j) mod 256]`, for `0 <= j < nb`.
The environment keeps these operands unchanged for the command's duration.

Emit exactly `na + nb - 1` output transactions. Output `k`, starting at zero,
corresponds to lag `k-(nb-1)` and is:

`out_data[k] = sat16(sum(A[i]*B[j] for i-j == k-(nb-1)) >> 15) mod 65536`,

where only in-range operand indices contribute, accumulation is exact in a
signed 64-bit carrier, `>>` is arithmetic shift (floor division by 32768),
and `sat16(x) = min(32767, max(-32768, x))`. There is no intermediate
saturation or wrapping. This equation also covers unequal lengths, either
length equal to one, negative values and saturated outputs. It does not
emit additional storage-layout padding before or after the overlapping lags.

## Reset Behavior

Reset is synchronous active-high. It cancels the pending command and output
frame. After reset, `in_ready=1`, `out_valid=0`, `out_data=0`,
`sample_req_valid=0` and `sample_req_addr=0`.
The kernel does not clear external memory. Memory initialization and
pending-response cancellation belong to the environment.
