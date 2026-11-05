# SampleTemperatureScale

Scale four binary32 logits by temperature and select between supplied greedy and random token candidates.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_modify_sample_temperature_scale (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_logit0,
    input  logic [31:0]  in_logit1,
    input  logic [31:0]  in_logit2,
    input  logic [31:0]  in_logit3,
    input  logic [31:0]  in_temperature,
    input  logic         in_all_random,
    input  logic [1:0]   in_greedy_token,
    input  logic [1:0]   in_random_token,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_logit0,
    output logic [31:0]  out_logit1,
    output logic [31:0]  out_logit2,
    output logic [31:0]  out_logit3,
    output logic [1:0]   out_sampled_token
);
```

### Behavior interface

```python
class AiMlSampleModifySampleTemperatureScale:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_logit0,
        in_logit1,
        in_logit2,
        in_logit3,
        in_temperature,
        in_all_random,
        in_greedy_token,
        in_random_token,
    ): ...
```

The Behavior arguments and returned fields are raw unsigned pin bit patterns; signed interpretation is specified below. One process call represents one accepted input. Each call returns exactly one output dictionary.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_logit0`, `out_logit1`, `out_logit2`, `out_logit3`: 32.
- `out_sampled_token`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_sample_modify_sample_temperature_scale`: the complete operator and its input/output handshake.

## Functional Behavior

EPS is binary32 0x3727C5AC (the rounded representation of 1e-5), and ONE is 0x3F800000. Using the binary32 comparison profile below, define

    greedy = (in_all_random == 0) and (in_temperature < EPS).
    divisor = ONE if greedy else in_temperature.

Output logit i is div(in_logit_i,divisor). Output token is in_greedy_token if greedy, otherwise in_random_token. Tokens are unsigned two-bit indices. They are supplied candidates; this module does not compute argmax, softmax, random sampling or top-k/top-p selection.

The all-random flag overrides greedy selection even when temperature is below EPS. At equality with EPS the random candidate is used. A NaN comparison is false. For temperature +0, greedy token 1 and random token 3: all_random=0 leaves normal logits unchanged and selects 1; all_random=1 performs division by +0 under the profile below and selects 3.

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
