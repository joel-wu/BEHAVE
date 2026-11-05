# MoE Expert Capacity

Compute the capacity of one expert bin from routed-token demand and a rational capacity factor. This is the capacity subfunction, not sorting, dispatch, padding data or the expert network.

## Interface

### RTL interface

```systemverilog
module ai_ml_moe_dispatch_moe_expert_padding #(
    parameter int TOKENS_WIDTH = 32,
    parameter int NUM_EXPERTS_WIDTH = 32,
    parameter int TOP_K_WIDTH = 32,
    parameter int WORLD_SIZE_WIDTH = 32,
    parameter int CAPACITY_FACTOR_NUM_WIDTH = 32,
    parameter int CAPACITY_FACTOR_DEN_WIDTH = 32,
    parameter int CAPACITY_WIDTH = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [TOKENS_WIDTH-1:0]            in_tokens,
    input  logic [NUM_EXPERTS_WIDTH-1:0]       in_num_experts,
    input  logic [TOP_K_WIDTH-1:0]             in_top_k,
    input  logic [WORLD_SIZE_WIDTH-1:0]        in_world_size,
    input  logic [CAPACITY_FACTOR_NUM_WIDTH-1:0] in_capacity_factor_num,
    input  logic [CAPACITY_FACTOR_DEN_WIDTH-1:0] in_capacity_factor_den,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [CAPACITY_WIDTH-1:0]          out_expert_capacity
);
```

### Behavior interface

```python
class AiMlMoeDispatchMoeExpertPadding:
    def __init__(self, capacity_width=32):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_tokens,
        in_num_experts,
        in_top_k,
        in_world_size,
        in_capacity_factor_num,
        in_capacity_factor_den,
    ):
        pass
```

The returned dictionary contains one key:
- `out_expert_capacity`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_expert_capacity`: CAPACITY_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor capacity_width matches RTL CAPACITY_WIDTH. Other input-width parameters
constrain the process arguments as specified by the ports.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

All ports are unsigned. RTL width parameters must be positive. Port widths bound inputs; arithmetic below is exact until the final encoding.

```
numerator   = in_top_k * in_tokens * in_world_size * in_capacity_factor_num
denominator = in_num_experts * in_capacity_factor_den
capacity    = 0 if denominator == 0 else numerator // denominator
out_expert_capacity = capacity mod 2**CAPACITY_WIDTH
```

There is one final floor, not a floor before applying the capacity factor. For tokens=10, experts=6, top_k=2, world_size=1 and factor=3/2 the capacity is 5, not 4. Factor zero produces zero; denominator zero is an explicit task extension returning zero. The upstream fallback that replaces a zero capacity with the maximum histogram count is outside this calculator because no histogram is supplied.

The rational factor makes the source capacity formula deterministic without binary floating-point evaluation; it does not require bitwise agreement with floating rounding at enormous counts.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
