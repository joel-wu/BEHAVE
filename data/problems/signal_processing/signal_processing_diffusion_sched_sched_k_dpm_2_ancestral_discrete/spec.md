# KDPM2AncestralEpsilonUpdate

Perform the two-stage, epsilon-prediction update of a KDPM2 ancestral diffusion
scheduler for one sample coordinate. A first-order midpoint stage retains the
original sample; the second stage uses a new model prediction, the retained
sample and explicit noise. Noise generation and schedule construction are
external so the task is deterministic and its trajectory can be reproduced.

## Interface

### RTL interface

```systemverilog
module signal_processing_diffusion_sched_sched_k_dpm_2_ancestral_discrete (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic in_phase,
    input logic [31:0] in_model_output,
    input logic [31:0] in_sample,
    input logic [31:0] in_noise,
    input logic [31:0] in_sigma,
    input logic [31:0] in_sigma_interpol,
    input logic [31:0] in_sigma_down,
    input logic [31:0] in_sigma_up,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_prev_sample,
    output logic [31:0] out_pred_original_sample,
    output logic [1:0] out_error
);
endmodule
```

Sample, model prediction, noise and both sample outputs are signed 32-bit
two's-complement Q16.16 lanes. The four sigma inputs are **unsigned** Q16.16.
`in_phase=0` requests the midpoint stage, `1` the second stage. `out_error`
is an unsigned status code. Every accepted request produces one response,
including an error. Transfer occurs on rising-edge valid-and-ready; a stalled
sender holds valid and payload. Responses are ordered, with no fixed latency.

### Behavior interface

```python
class SignalProcessingDiffusionSchedSchedKDpm2AncestralDiscrete:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_phase, in_model_output, in_sample, in_noise,
                in_sigma, in_sigma_interpol, in_sigma_down, in_sigma_up): ...
```

Arguments are unsigned bus encodings. One dictionary contains `out_prev_sample`,
`out_pred_original_sample` and `out_error`, also as bus encodings.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 2.
- `out_pred_original_sample`, `out_prev_sample`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_diffusion_sched_sched_k_dpm_2_ancestral_discrete`:
  paired-stage epsilon-prediction update with one saved sample coordinate.

## Functional Behavior

Let `Q=65536`; use signed lane integers `x` (sample), `e` (model output) and
`z` (noise). Let `quant(n)` mean clip `floor(n/Q)` to the signed 32-bit range
then encode its two's-complement word. All numerators are evaluated exactly,
without intermediate clipping. Only the two returned samples are quantized.

Initially the block expects phase zero. A successful phase-zero request requires
`sigma>0`, `0<sigma_interpol<=sigma`, and `sigma_down<=sigma_interpol`.
`sigma_up` can be any unsigned input. These are supplied schedule coefficients,
not inferred from the sample or an ignored timestep tag. This unit does not
construct or certify the global noise schedule.

On phase zero, save `x0=x` and all four coefficients, set the expected phase to
one, and return:

- `out_pred_original_sample = quant(Q*x - sigma*e)`;
- `out_prev_sample = quant(Q*x + (sigma_interpol-sigma)*e)`.

The first-stage noise input has no effect. The next successful phase-one
request uses its **new** `x,e,z` but the saved coefficients; its four coefficient
inputs are ignored. It returns:

- `out_pred_original_sample = quant(Q*x - saved_sigma_interpol*e)`;
- `out_prev_sample = quant(Q*x0 + (saved_sigma_down-saved_sigma)*e + saved_sigma_up*z)`.

It then expects phase zero again. The second-stage update uses the original
saved sample, not the caller's midpoint sample. For epsilon prediction the
real-valued source derivative `(x-(x-sigma*e))/sigma` is exactly `e`; these
equations evaluate that real update before the specified final fixed-point
rounding. They do not claim bit equivalence with a floating-point tensor path.
The `v_prediction` variant and a complete diffusion model are outside this unit.

`out_error=0` on success. A request with the wrong phase returns code 1;
otherwise invalid phase-zero coefficients return code 2. Errors return both
sample outputs as zero and leave all algorithm state unchanged. Thus every bus
pattern has a defined result. The requester may correct and retry an error.

For real-valued coefficients `(sigma,interpol,down,up)=(2,1,0.5,0.25)`,
phase zero with `x=3,e=0.5` returns `(prev,pred)=(2.5,2)`.
Phase one with `x=2.5,e=0.25,z=2` returns `(3.125,2.25)`.
All values in this example are exactly representable in Q16.16.

## Reset Behavior

Synchronous active-high `rst` overrides transfers. It clears the saved sample
and coefficients, restores phase zero, cancels in-flight/pending responses,
and sets `out_valid=0`, all output payloads to zero, and `in_ready=1`.
A phase-one request after
reset is an order error, not a continuation of a pre-reset trajectory.
