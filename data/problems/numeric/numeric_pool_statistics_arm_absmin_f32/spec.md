# Absolute Minimum of Four FP32 Samples

AbsminF32x4 computes the absolute minimum of 4 binary32 samples and its first index, using the scalar left-to-right
ordered-comparison recurrence. Input transport and memory boundaries are
explicit below; no transaction changes the numerical state of another.

## Interface

### RTL interface

```systemverilog
module absmin_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_result,
    output logic [1:0]  out_index,

    output logic        src_req_valid,
    output logic [1:0]  src_req_addr,
    input  logic        src_req_ready,
    input  logic        src_resp_valid,
    input  logic [31:0] src_resp_rdata
);
```

`src` is a read-only external memory of four 32-bit words addressed by `in_base_addr + k` for `k = 0,1,2,3`, with address arithmetic wrapping modulo 4 because the address port is 2 bits wide.

### Behavior interface

```python
class AbsminF32x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_base_addr):
        pass
```

`process()` returns exactly one output transaction per accepted input:
```python
{"out_result": ..., "out_index": ...}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_index`: 2.
- `out_result`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.src is the external read-only memory view: 4 unsigned 32-bit words. The execution
environment supplies its contents before processing; it is not an additional process
argument or output.
Construction creates zero-filled default storage. `reset()` preserves the current
contents and binding of `self.src`. A new independent run installs its configured
image; a reset within that run does not reinstall it.

## Modules

- `absmin_f32x4`: fixed-length absolute-minimum reducer.

## Functional Behavior

Every accepted input supplies one base address into a read-only 4-word memory and produces exactly one
ordered output. Each word is an IEEE-754 binary32 bit pattern carried as an
unsigned 32-bit integer. The scan reads `src[(in_base_addr+i) mod 4]`
for `i=0..3` in that order.

For each scanned word, clear bit 31 to obtain its absolute magnitude. Initialize
`best` to the first magnitude and `index` to zero. For each subsequent position
`i`, replace both with `(magnitude,i)` only when that magnitude is strictly
less than `best` under ordered binary32 comparison.
The output `out_result` is the final magnitude. `out_index` is its offset in the
scanned block (0 through 3), not the absolute memory address. Ties retain
the earliest position.

The comparison is false if either operand is NaN. Thus a NaN in position zero
remains the result; later NaNs never replace the current result. Absolute
value clears only the sign bit, preserving NaN payload and quiet/signaling
bits. Either signed zero becomes positive zero, either infinity becomes
positive infinity, and subnormals retain their magnitudes without flushing.
For non-NaN, sign-cleared words, unsigned bit-pattern ordering is exactly
numeric ordering.

Input and output transfers occur on rising edges with valid and ready high.
An offered input remains stable until accepted. Pending output validity and
payload remain stable while `out_ready` is low. Internal latency is not fixed.
Reset discards pending commands and outputs.

External memory `src` is a 4-word image bound before the run and fixed
throughout it.
Read addresses wrap modulo 4. Each `src_req_valid && src_req_ready`
edge accepts one request, whose address remains stable while stalled.
Exactly one ordered response pulse `src_resp_valid` arrives for each accepted
request, at least one cycle later, with its data on `src_resp_rdata`. Reset
cancels pending requests and responses on both sides without changing the
bound image.

## Reset Behavior

Reset is synchronous active-high.

On reset:

- `out_valid` = `0`
- `out_result` = `32'h00000000`
- `out_index` = `2'b00`
- `src_req_valid` = `0`
- `src_req_addr` = `2'b00`

Architectural state after reset:

- no input transaction is in flight
- no output transaction is pending
- the stored current best value is `32'h00000000`
- the stored current best index is `2'b00`
- the stored current-best-NaN flag is `0`
- the stored base address is `2'b00`
- the issued-read count is `0`
- the received-read count is `0`

The external memory image is bound by the environment before execution. Reset cancels pending responses but does not change that image.
