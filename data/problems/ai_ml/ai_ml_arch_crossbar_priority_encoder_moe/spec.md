# Eight-Request Least-Significant-Bit Priority Encoder

This block performs a fixed-size priority encode over a vector of request bits. For each accepted input transaction, it determines whether any request is asserted, selects the lowest-numbered asserted request, and returns both the binary index and the corresponding one-hot grant. The selection rule is strict least-significant-bit priority: bit 0 has highest priority, bit 7 has lowest priority.

## Interface

### RTL interface

```systemverilog
module ai_ml_arch_crossbar_priority_encoder_moe (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_req,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_found,
    output logic [2:0]  out_index,
    output logic [7:0]  out_grant
);
```

### Behavior interface

```python
class AiMlArchCrossbarPriorityEncoderMoe:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_req): ...
```

Each call represents one accepted request and returns exactly one dictionary
with keys:
- `out_found`
- `out_index`
- `out_grant`

All values are Python integers: `out_found` is 0 or 1, `out_index` is in [0, 7],
and `out_grant` is in [0, 255]. `in_req` is likewise an unsigned integer in
[0, 255]. Return all three fields even when no request is set, not `None` or
a list. There is no persistent priority state; `reset()` returns no response.

## Modules

- `ai_ml_arch_crossbar_priority_encoder_moe`: top-level priority encoder.

## Functional Behavior

`in_req` is an 8-bit unsigned request vector. Bit position `i` corresponds to request `i`.

For each accepted input transaction, the block computes:

1. `out_found`  
   `out_found = 1` if any bit of `in_req` is `1`.  
   `out_found = 0` if `in_req == 8'b00000000`.

2. `out_index`  
   If `out_found = 1`, `out_index` is the binary encoding of the lowest-numbered asserted bit in `in_req`. Formally, `out_index` is the smallest integer `i` in the inclusive range `[0, 7]` such that `in_req[i] = 1`.

   Priority order is:
   - if `in_req[0] = 1`, `out_index = 0`
   - else if `in_req[1] = 1`, `out_index = 1`
   - else if `in_req[2] = 1`, `out_index = 2`
   - else if `in_req[3] = 1`, `out_index = 3`
   - else if `in_req[4] = 1`, `out_index = 4`
   - else if `in_req[5] = 1`, `out_index = 5`
   - else if `in_req[6] = 1`, `out_index = 6`
   - else `out_index = 7`

   If `out_found = 0`, `out_index = 3'b000`.

3. `out_grant`  
   If `out_found = 1`, `out_grant` is the one-hot encoding of `out_index`:
   `out_grant = 8'b00000001 << out_index`.

   Equivalently, `out_grant` contains only the selected least-significant asserted request bit and clears all other bits:
   `out_grant = in_req & (-in_req)` in 8-bit two's-complement arithmetic.

   If `out_found = 0`, `out_grant = 8'b00000000`.

All outputs are unsigned integer bit patterns with widths matching their ports.

Examples:
- `in_req = 8'b00000000` -> `out_found = 0`, `out_index = 3'b000`, `out_grant = 8'b00000000`
- `in_req = 8'b00010000` -> `out_found = 1`, `out_index = 3'b100`, `out_grant = 8'b00010000`
- `in_req = 8'b10110000` -> `out_found = 1`, `out_index = 3'b100`, `out_grant = 8'b00010000`
- `in_req = 8'b00000111` -> `out_found = 1`, `out_index = 3'b000`, `out_grant = 8'b00000001`

The block produces exactly one output transaction per accepted input,
including the all-zero request mask. The legacy identifier is retained, but
this task is an arbitration primitive, not a mixture-of-experts router or
score-based top-k selection.

Input and output transfers occur at rising edges with the corresponding
valid and ready asserted and reset inactive. The source holds a stalled
request stable; the block holds all fields of a stalled response stable.
Responses are ordered. Backpressure and arbitrary internal latency are
allowed; simultaneous retirement and acceptance are allowed. Payload values
when `out_valid` is 0 are don't-care, not another transaction.

## Reset Behavior

Reset is asynchronous, active high. Assertion cancels any pending response,
sets `out_valid` and all payload fields to 0, and makes `in_ready` 1. No
transaction transfers while reset is asserted. After release, `out_valid`
remains 0 until a new request produces a response. The priority function has
no algorithmic state; its transaction-storage registers do reset.
