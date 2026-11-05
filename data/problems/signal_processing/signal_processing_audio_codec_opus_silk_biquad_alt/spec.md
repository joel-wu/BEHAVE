# SignalProcessingAudioCodecOpusSilkBiquadAlt

This block implements the SILK alternative second-order ARMA biquad in direct-form-II transposed structure. Each accepted transaction processes one stereo sample pair using independent channel states. It retains the SILK Q-format scaling, coefficient splitting, state-update rounding and saturated outputs, with explicit wraparound for arbitrary coefficient bit patterns.

## Interface

### RTL interface

```systemverilog
module signal_processing_audio_codec_opus_silk_biquad_alt (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [15:0] in_sample0,
    input  logic  [15:0] in_sample1,
    input  logic  [31:0] in_B0_Q28,
    input  logic  [31:0] in_B1_Q28,
    input  logic  [31:0] in_B2_Q28,
    input  logic  [31:0] in_A0_Q28,
    input  logic  [31:0] in_A1_Q28,
    input  logic         in_clear_state,

    output logic         out_valid,
    input  logic         out_ready,
    output logic  [15:0] out_sample0,
    output logic  [15:0] out_sample1
);
```

### Behavior interface

```python
class SignalProcessingAudioCodecOpusSilkBiquadAlt:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_sample0,
            in_sample1,
            in_B0_Q28,
            in_B1_Q28,
            in_B2_Q28,
            in_A0_Q28,
            in_A1_Q28,
            in_clear_state):
        pass
```

Each call represents one accepted stereo pair, updates the four channel states,
and returns exactly one dictionary with keys `out_sample0` and `out_sample1`.
The two values are Python integers in [0, 65535], encoding signed 16-bit samples
in one response, not two response records. Input samples use the same encoding;
coefficients use unsigned 32-bit encodings and `in_clear_state` is 0 or 1.
Return a dictionary even for silence, not `None` or a list. State persists
between calls. `reset()` clears all four states and returns no response;
`in_clear_state` instead clears them before processing its own sample pair.

## Modules

- `signal_processing_audio_codec_opus_silk_biquad_alt`: top-level stride-2 biquad processing one sample pair per accepted input transaction and maintaining four Q12 state registers.

## Functional Behavior

All ports are integer bit patterns.

`in_sample0` and `in_sample1` are signed 16-bit input samples in Q0.  
`in_B0_Q28`, `in_B1_Q28`, `in_B2_Q28`, `in_A0_Q28`, and `in_A1_Q28` are signed 32-bit coefficients in Q28.  
The architectural state is four signed 32-bit registers `S0`, `S1`, `S2`, `S3`, each representing a Q12 value. `S0`/`S1` belong to channel 0 and `S2`/`S3` belong to channel 1.

For each accepted input transaction:

1. If `in_clear_state != 0`, the old state used for this transaction is
   `S0 = 0`, `S1 = 0`, `S2 = 0`, `S3 = 0`.
   Otherwise the old state is the stored architectural state.

2. Interpret the coefficient ports as signed 32-bit integers and form the negated AR coefficients:
   - `negA0 = -A0_Q28`
   - `negA1 = -A1_Q28`
   Both negations wrap modulo `2^32`, then are interpreted as signed 32-bit values for subsequent arithmetic.

3. Split the negated AR coefficients into lower and upper parts:
   - `A0_L_Q28 = negA0 & 16'h3FFF`
   - `A0_U_Q28 = arithmetic_right_shift(negA0, 14)`
   - `A1_L_Q28 = negA1 & 16'h3FFF`
   - `A1_U_Q28 = arithmetic_right_shift(negA1, 14)`
   `A*_L_Q28` are non-negative 14-bit integers represented on 32 bits. `A*_U_Q28` are signed 32-bit integers.

4. For each channel independently, compute the intermediate output:
   - `out32_Q14_ch0 = (SMLAWB(S0, B0_Q28, sample0)) << 2`
   - `out32_Q14_ch1 = (SMLAWB(S2, B0_Q28, sample1)) << 2`
   The left shift by 2 is a 32-bit wraparound shift. The result is interpreted as signed 32-bit.

5. Update the next state for channel 0 in this exact order using the old state and `out32_Q14_ch0`:
   - `next_S0 = S1 + RSHIFT_ROUND(SMULWB(out32_Q14_ch0, A0_L_Q28), 14)`
   - `next_S0 = SMLAWB(next_S0, out32_Q14_ch0, A0_U_Q28)`
   - `next_S0 = SMLAWB(next_S0, B1_Q28, sample0)`
   - `next_S1 = RSHIFT_ROUND(SMULWB(out32_Q14_ch0, A1_L_Q28), 14)`
   - `next_S1 = SMLAWB(next_S1, out32_Q14_ch0, A1_U_Q28)`
   - `next_S1 = SMLAWB(next_S1, B2_Q28, sample0)`

6. Update the next state for channel 1 in this exact order using the old state and `out32_Q14_ch1`:
   - `next_S2 = S3 + RSHIFT_ROUND(SMULWB(out32_Q14_ch1, A0_L_Q28), 14)`
   - `next_S2 = SMLAWB(next_S2, out32_Q14_ch1, A0_U_Q28)`
   - `next_S2 = SMLAWB(next_S2, B1_Q28, sample1)`
   - `next_S3 = RSHIFT_ROUND(SMULWB(out32_Q14_ch1, A1_L_Q28), 14)`
   - `next_S3 = SMLAWB(next_S3, out32_Q14_ch1, A1_U_Q28)`
   - `next_S3 = SMLAWB(next_S3, B2_Q28, sample1)`

7. Produce the two output samples by scaling `out32_Q14` back to Q0 with truncation toward negative infinity after adding `(1<<14)-1`, then saturating to signed 16-bit:
   - `tmp0 = arithmetic_right_shift(out32_Q14_ch0 + 16383, 14)`
   - `tmp1 = arithmetic_right_shift(out32_Q14_ch1 + 16383, 14)`
   - `out_sample0 = SAT16(tmp0)`
   - `out_sample1 = SAT16(tmp1)`
   `SAT16(x)` returns:
   - `32767` if `x > 32767`
   - `-32768` if `x < -32768`
   - otherwise `x`

8. After output computation, store:
   - `S0 = next_S0`
   - `S1 = next_S1`
   - `S2 = next_S2`
   - `S3 = next_S3`

Arithmetic primitives are defined exactly as follows, with all inputs first interpreted as signed 32-bit integers and every result wrapped to signed 32-bit unless otherwise stated:

- `arithmetic_right_shift(x, n)` with `n = 14` means sign-extending shift right by 14 bits.
- `SMULWB(a, b) = arithmetic_right_shift(a * sign_extend_16(b & 16'hFFFF), 16)`
  where the multiplication is performed in unbounded signed integer precision before the right shift, then the final result wraps to signed 32-bit.
- `SMLAWB(a, b, c) = a + arithmetic_right_shift(b * sign_extend_16(c & 16'hFFFF), 16)`
  where the multiplication and addition are performed in unbounded signed integer precision, then the final result wraps to signed 32-bit.
- `RSHIFT_ROUND(x,14) = ((x >> 13) + 1) >> 1`, with arithmetic right shifts.
  Rounding is to nearest with ties toward positive infinity, including for
  negative `x`: `-8192` becomes `0`, not `-1`.
- Only the final output conversion `out32_Q14 + 16383` uses a widened signed
  sum before its shift and saturation; it does not wrap at 32 bits.

Worked example for the output scaling rule:
- if `out32_Q14 = 16384`, then `tmp = (16384 + 16383) >> 14 = 1`
- if `out32_Q14 = -16384`, then `tmp = (-16384 + 16383) >> 14 = -1`

The block is a 1:1 transaction mapper: every accepted input transaction produces exactly one output transaction.
Transfers require valid and ready. Pending outputs hold their values and valid
flag under backpressure. Filter state updates only on accepted inputs or reset.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:
- `S0 = 0`
- `S1 = 0`
- `S2 = 0`
- `S3 = 0`

Outputs have no retained architectural state. After reset, the next accepted input transaction is processed using the zeroed state unless `in_clear_state` is asserted, in which case the state is also zero for that transaction. `out_sample0` and `out_sample1` are defined only on output transactions.
