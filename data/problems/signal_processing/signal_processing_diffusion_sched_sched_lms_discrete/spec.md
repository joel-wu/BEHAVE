# SignalProcessingDiffusionSchedSchedLmsDiscrete

A scalar integer adaptation of epsilon-prediction linear-multistep diffusion
sampling. It integrates over a descending unit-spaced sigma grid and retains
up to four derivatives. The task is the scheduler step, not a diffusion model
or the preparation of a model's scaled input.

## Interface

### RTL interface

```systemverilog
module signal_processing_diffusion_sched_sched_lms_discrete (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_model_output,
    input  logic [31:0] in_timestep,
    input  logic [31:0] in_sample,
    input  logic [2:0]  in_order,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_prev_sample,
    output logic [31:0] out_pred_original_sample
);
```

Transfers occur on rising edges with valid and ready both high. Each input
produces one ordered output. Unaccepted output data and valid remain stable.
`in_model_output` and `in_sample` are signed 32-bit integer bit patterns;
`in_timestep` and `in_order` are unsigned. Outputs are 32-bit patterns.

### Behavior interface

```python
class SignalProcessingDiffusionSchedSchedLmsDiscrete:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_model_output, in_timestep, in_sample, in_order): ...
```

The returned record contains both named output fields.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pred_original_sample`, `out_prev_sample`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_diffusion_sched_sched_lms_discrete`: scheduler and history.

## Functional Behavior

The fixed grid is sigma[i]=1000-i for i=0,...,1000. The first accepted
transaction selects index i=min(in_timestep,1000). Later transactions use
the stored index; their timestep fields are ignored. This field is an index,
not a training-noise timestep or an IEEE floating-point value.

At i<1000, let x and e be the signed sample and model output. Compute the
predicted original sample p=x-sigma[i]*e in exact integer arithmetic. The
ODE derivative (x-p)/sigma[i] is exactly e. Insert e as the newest derivative,
retaining the four most recent values. Set k to the smaller of the available
history length and in_order clamped to [1,4].

The integration coefficients, newest derivative first, are:

| k | Coefficients |
|---|---|
| 1 | -1 |
| 2 | -3/2, 1/2 |
| 3 | -23/12, 16/12, -5/12 |
| 4 | -55/24, 59/24, -37/24, 9/24 |

These integrate the derivative interpolation polynomial from sigma[i] to
sigma[i+1]. For this integer task, truncate each coefficient-times-derivative
term toward zero separately. The previous sample is x plus the sum of these
terms. Return the low 32 bits of the previous sample and p; increment i by one.
History is retained up to depth four even when a smaller order is requested.

At i=1000, return x for both outputs and retain the exhausted index and history.
This explicit terminal behavior avoids a division at sigma=0; reset starts a
new trajectory. The model output is not generated or modified by this block.

Example: a first input with index998, x=20, e=3 yields previous=17 and p=14.
The next input x=17, e=2, order2 uses sigma=1 and history (2,3), giving
previous=15 and p=15. Subsequent inputs leave the supplied sample unchanged.

## Reset Behavior

Synchronous active-high reset clears pending output, initialization, index,
history and count. Both output fields and `out_valid` clear to zero. Reset
consumes no input; the next accepted input selects a fresh starting index.
