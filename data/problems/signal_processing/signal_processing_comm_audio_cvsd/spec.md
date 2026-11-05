# signal_processing_comm_audio_cvsd

`signal_processing_comm_audio_cvsd` is a single-state continuously variable slope delta (CVSD) sample processor. Each accepted input transaction performs exactly one CVSD step in either encoder mode or decoder mode and produces exactly one output transaction. The block maintains the CVSD history register, adaptive step size, reference integrator, and the signal-conditioning filter state. Encoder mode applies a first-order pre-emphasis filter to the signed PCM input, compares the filtered sample against the current reference, updates the adaptive slope and reference, and returns the generated 1-bit CVSD symbol. Decoder mode accepts one CVSD symbol bit, updates the same adaptive slope and reference, applies the second-order post-emphasis/DC-blocking filter to the updated reference, and returns one signed PCM output sample.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_audio_cvsd (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic       in_mode,
    input  logic [7:0] in_data,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
```

Port definitions:

- `in_mode == 1'b0`: encode one PCM sample to one CVSD bit.
  - `in_data` is one signed 8-bit PCM sample in two's complement, range [-128, +127].
  - `out_data[0]` is the generated CVSD bit.
  - `out_data[7:1]` shall be zero.
- `in_mode == 1'b1`: decode one CVSD bit to one PCM sample.
  - `in_data[0]` is the input CVSD bit.
  - `in_data[7:1]` are ignored.
  - `out_data` is one signed 8-bit PCM sample in two's complement, range [-128, +127].

The block is a 1:1 transaction mapper: each accepted input produces exactly one ordered output. Outside reset, acceptance and transfer occur on rising edges with `in_valid && in_ready` and `out_valid && out_ready`, respectively. A stalled valid payload must remain stable; state changes only on accepted inputs.

`rst` is synchronous active-high.

### Behavior interface

```python
class SignalProcessingCommAudioCvsd:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_mode, in_data):
        pass
```

One `process(...)` call represents one accepted input and returns exactly
`{"out_data": value}`. Arguments are unsigned pin words: one-bit `in_mode`
and eight-bit `in_data`. The result is an unsigned eight-bit word, encoding
either the generated bit or the signed PCM sample as described above. Both
modes, including zero results, return the same one-field dictionary, never
`None`. Keep one instance across calls, including mode changes, to preserve
shared adaptive state and the mode-specific filters. `reset()` returns `None`
and restores the state below. Calls do not represent stalled clock cycles.

## Modules

- `signal_processing_comm_audio_cvsd`: top-level CVSD encoder/decoder with shared adaptive-state machine and fixed signal-conditioning filters.

## Functional Behavior

The block uses signed Q28 arithmetic with scale `Q = 2^28`. Its encoded
constants are:

- history length `NUM_BITS = 3`
- history mask `BITMASK = 0x07`
- `ONE_Q = 268435456`
- `DELTA_MIN_Q = 2684354`
- `ALPHA_Q = 241591910`
- `APLUSB_Q = 507343011`
- `APRODB_Q = 239175991`

The coefficients use `alpha=9/10` and `beta=99/100`: encode `alpha`, `alpha+beta` and `alpha*beta` by multiplying each exact rational value by `Q` and rounding down independently. The minimum step is `floor(Q/100)`.

Q28 multiplication is `(a*b) >>> 28`. The `1.5` slope increase is
`delta + (delta >>> 1)`, and the reciprocal update truncates `2*delta/3`
toward zero. These integer rules, rather than ideal real arithmetic, define
the required behavior.

Architectural state:

- `bitref`: 3-bit unsigned shift register holding the most recent CVSD bits.
- `ref`: signed Q28 reference, limited to `[-ONE_Q, ONE_Q]`.
- `delta`: signed Q28 adaptive step, limited to
  `[DELTA_MIN_Q, ONE_Q]`.
- pre-emphasis filter state `prefilt_x1`, storing the previous encoder PCM
  sample in Q28.
- post-emphasis filter states `post_x1`, `post_y1`, `post_y2`.

Input sample conversion for encode mode:

- Interpret `in_data` as signed 8-bit two's complement integer `x_pcm` in [-128, +127].
- Convert to Q28 as `x_q = x_pcm << 21`.
- Therefore `0x40` represents `+0.5`, `0x80` represents `-1.0`, and `0x7F`
  represents `127/128` exactly in Q28.

### Encode mode (`in_mode == 0`)

1. Compute the pre-emphasis filter output
   - `y_q = x_q - qmul_q28(ALPHA_Q, prefilt_x1)`
   - `prefilt_x1` is the previous encoder sample in Q28.
   - Decoder transactions do not modify `prefilt_x1`.

2. Generate the output CVSD bit from the current reference before any state update:
   - `bit = 0` if `ref > y_q`
   - `bit = 1` otherwise
   - Equality produces `bit = 1`.

3. Update the bit history:
   - `bitref = ((bitref << 1) | bit) & BITMASK`

4. Update the adaptive step size from the new `bitref`:
   - if `bitref == 0` or `bitref == BITMASK`, then
     `delta = delta + (delta >>> 1)`
   - otherwise `delta = trunc_toward_zero(2*delta/3)`

5. Clamp the step size:
   - clamp `delta` to `[DELTA_MIN_Q, ONE_Q]`

6. Update the reference from the new `delta`:
   - if `bit == 1`, `ref = ref + delta`
   - else `ref = ref - delta`

7. Clamp the reference:
   - clamp `ref` to `[-ONE_Q, ONE_Q]`

8. Update encoder filter state:
   - `prefilt_x1 = x_q`

9. Produce one output transaction:
   - `out_data = bit`
   - encoded as an 8-bit unsigned value with bits `[7:1] = 0`.

Worked encode example from reset with input `in_mode=0`, `in_data=0x00`:
- initial `prefilt_x1 = 0`, `ref = 0`, `delta = DELTA_MIN_Q`, `bitref = 0`
- `x_q = 0`, `y_q = 0`
- `ref > y_q` is false, so `bit = 1`
- new `bitref = 1`
- `trunc_toward_zero(2*delta/3) < DELTA_MIN_Q`, so the new `delta` is
  `DELTA_MIN_Q`
- new `ref = DELTA_MIN_Q`
- output `out_data = 0x01`

### Decode mode (`in_mode == 1`)

1. Extract the input bit:
   - `bit = in_data & 1`

2. Update the bit history:
   - `bitref = ((bitref << 1) | bit) & BITMASK`

3. Update the adaptive step size from the new `bitref`:
   - if `bitref == 0` or `bitref == BITMASK`, then
     `delta = delta + (delta >>> 1)`
   - otherwise `delta = trunc_toward_zero(2*delta/3)`

4. Clamp the step size:
   - clamp `delta` to `[DELTA_MIN_Q, ONE_Q]`

5. Update the reference from the new `delta`:
   - if `bit == 1`, `ref = ref + delta`
   - else `ref = ref - delta`

6. Clamp the reference:
   - clamp `ref` to `[-ONE_Q, ONE_Q]`

7. Compute the post-emphasis/DC-blocking filter output using the updated reference:
   - The fixed-point difference equation is
     `y_q = ref - post_x1 + qmul_q28(APLUSB_Q, post_y1) - qmul_q28(APRODB_Q, post_y2)`.
   - `post_x1` stores the previous decoder input to this filter, which is the previous accepted decoder transaction's clamped `ref`.
   - `post_y1` stores the previous decoder filter output.
   - `post_y2` stores the decoder filter output from two accepted decoder transactions earlier.
   - Encoder transactions do not modify `post_x1`, `post_y1`, or `post_y2`.

8. Update decoder filter state:
   - `post_x1 = ref`
   - `post_y2 = post_y1`
   - `post_y1 = y_q`

9. Convert the Q28 decoder output to signed 8-bit PCM:
   - `q = trunc_toward_zero(y_q / 2^21)`
   - if `q > 127`, set `q = 127`
   - else if `q < -128`, set `q = -128`

10. Produce one output transaction:
    - `out_data` is the 8-bit two's complement encoding of signed integer `q`.

Worked decode example from reset with input `in_mode=1`, `in_data=0x01`:
- initial `ref = 0`, `delta = DELTA_MIN_Q`, `bitref = 0`, and all post-filter state is zero
- `bit = 1`
- new `bitref = 1`
- the new `delta` is clamped to `DELTA_MIN_Q`
- new `ref = DELTA_MIN_Q`
- `y_q = DELTA_MIN_Q`
- `q = trunc_toward_zero(DELTA_MIN_Q / 2^21) = 1`
- output `out_data = 0x01`

### Transaction ordering and shared state

Encode and decode transactions share `bitref`, `delta`, and `ref`. Therefore an accepted transaction in one mode changes the subsequent behavior of the other mode. The filter states are mode-specific:
- only encode transactions update `prefilt_x1`
- only decode transactions update `post_x1`, `post_y1`, `post_y2`

No other state exists.

## Reset Behavior

On any cycle with `rst == 1` at the rising edge, the block enters the reset state.

Architectural state after reset:

- `bitref = 0`
- `ref = 0`
- `delta = DELTA_MIN_Q`
- `prefilt_x1 = 0`
- `post_x1 = 0`
- `post_y1 = 0`
- `post_y2 = 0`

Output/reset handshake state:

- `out_valid = 0`
- `out_data = 0x00`

After reset, the next accepted input transaction is processed using exactly the reset-state values above.
