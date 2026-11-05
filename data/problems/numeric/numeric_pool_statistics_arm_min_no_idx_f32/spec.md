# Vector Minimum Without Index FP32

This module computes the minimum value of a fixed 16-element FP32 vector stored in external memory and returns only the minimum value, not its index. The reduction is defined as a left-to-right scan initialized with the FP32 bit pattern for the largest finite positive number, and each element updates the running minimum only when the current minimum is strictly greater than the new element under ordinary IEEE-754 ordered comparison. This is a memory-bound reduction kernel with one command transaction producing one result transaction.

## Interface

### RTL interface

```systemverilog
module vector_min_no_idx_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_min_value,

    output logic        src_req_valid,
    output logic [3:0]  src_req_addr,
    input  logic        src_req_ready,
    input  logic        src_resp_valid,
    input  logic [31:0] src_resp_rdata
);
endmodule
```

### Behavior interface

```python
class VectorMinNoIdxFp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_base_addr):
        pass
```

The `process()` return value is a dict with key:
- `out_min_value`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_min_value`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

self.src is the external read-only memory view: 16 unsigned 32-bit words. The
execution environment supplies its contents before processing; it is not an additional
process argument or output.
Construction creates zero-filled default storage. `reset()` preserves the current
contents and binding of `self.src`. A new independent run installs its configured
image; a reset within that run does not reinstall it.

## Modules

- `vector_min_no_idx_fp32`: top-level reduction kernel. Accepts a base address, reads 16 consecutive FP32 words from external memory `src`, computes the minimum by sequential comparison, and emits one FP32 result.

## Functional Behavior

One accepted input transaction supplies `in_base_addr`, a 4-bit unsigned base address into the external memory `src`. The kernel reads exactly 16 consecutive elements from addresses:

- `in_base_addr + 0`
- `in_base_addr + 1`
- ...
- `in_base_addr + 15`

All addresses are 4-bit unsigned. Address calculation wraps modulo 16; equivalently, each effective address is `(in_base_addr + i) & 4'hF`.

Each memory word is a 32-bit FP32 bit pattern. The output `out_min_value` is also a 32-bit FP32 bit pattern.

The reduction is defined by the following ordered recurrence:

- Initial running minimum `min_value = 32'h7F7FFFFF`
- For `i = 0` to `15` in increasing order:
  - `new_val = src[(in_base_addr + i) mod 16]`
  - If `min_value > new_val`, then `min_value = new_val`
  - Otherwise `min_value` is unchanged
- After processing all 16 elements, `out_min_value = min_value`

The comparison `min_value > new_val` is an IEEE-754 ordered greater-than comparison on FP32 values with these required semantics:

- If either operand is NaN, the comparison is false.
- `+Inf` compares greater than every finite value and `-Inf`.
- `-Inf` compares less than every finite value and `+Inf`.
- `+0` and `-0` compare equal, so neither is greater than the other.
- Subnormal values participate in ordinary numerical ordering; they are not flushed. The emitted result preserves the selected word's original 32 bits.

Consequences of the ordered scan:

- If all 16 elements are ordinary finite numbers and contain no NaN, the output is the numeric minimum of the 16 values.
- A NaN element never updates the running minimum.
- If every element is NaN, the running minimum remains at `32'h7F7FFFFF`, so the output is `32'h7F7FFFFF`.
- If the first non-NaN element is `+0` and a later element is `-0`, the result remains `+0` because `+0 > -0` is false.
- If the first non-NaN element is `-0`, later `+0` does not replace it.

The kernel produces exactly one output transaction for each accepted input transaction.

External memory `src` is a read-only 16-word image bound before the run; it is
not an internal all-zero constant. The environment holds the image fixed for
the run. Each read request is accepted on a rising edge with `src_req_valid`
and `src_req_ready` high; the address remains stable until then. Responses are
ordered, with exactly one `src_resp_valid` pulse per accepted request, at least
one cycle later. The response data is valid with that pulse. Input commands
and result transactions use the same valid/ready acceptance rule, and a
pending result remains valid and stable under output backpressure. Reset
cancels outstanding commands, requests, responses and outputs on both sides;
it does not replace the externally bound memory image.

## Reset Behavior

Reset is synchronous active-high. On any cycle with `rst = 1`, the architectural state is reinitialized as follows:

- No command is in flight.
- No memory request is in flight.
- No unread memory response is buffered.
- No output result is pending.
- `in_ready = 1`
- `out_valid = 0`
- `out_min_value = 32'h00000000`
- `src_req_valid = 0`
- `src_req_addr = 4'h0`

Internal architectural state after reset:

- Stored base address = `4'h0`
- Element counter = `0`
- Running minimum register = `32'h7F7FFFFF`
- Output holding register = `32'h00000000`
