# SignalProcessingAudioCodecOpusSilkAnaFiltBank

This block implements one stage of a 2-band analysis filter bank for signed 16-bit PCM audio. Each accepted input transaction consumes exactly two consecutive input samples, updates a 2-word internal state vector, and produces one low-band sample and one high-band sample. The algorithm is the fixed-point first-order all-pass pair used for SILK analysis filtering: the even-indexed sample is processed by one all-pass section, the odd-indexed sample by a second all-pass section, and the two section outputs are then summed and differenced to form the decimated low and high bands.

## Interface

### RTL interface

```systemverilog
module signal_processing_audio_codec_opus_silk_ana_filt_bank (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_s0,
    input  logic [15:0]  in_s1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_outL,
    output logic [15:0]  out_outH
);
```

### Behavior interface

```python
class SignalProcessingAudioCodecOpusSilkAnaFiltBank:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_s0, in_s1): ...
```

Each call consumes one accepted two-sample pair and returns one `dict` with
exactly `out_outL` and `out_outH`. Inputs and field values are Python integers
in 0..65535 encoding signed 16-bit PCM port patterns. Both bands belong to one
response, not two records. Zero output still returns both fields, never `None`
or an empty list. The two Q10 state words persist across calls; `reset()` clears
them without producing a response.

## Modules

- `signal_processing_audio_codec_opus_silk_ana_filt_bank`: top-level single-clock analysis filter bank with internal 2-word state.

## Functional Behavior

All arithmetic is signed two's-complement integer arithmetic.

Input ports `in_s0` and `in_s1` are signed 16-bit PCM samples. They represent two consecutive input samples: `in_s0` is the even-indexed sample of the pair and `in_s1` is the odd-indexed sample of the pair.

The block contains two architectural state registers:

- `S[0]`: signed 32-bit state for the even-sample all-pass section.
- `S[1]`: signed 32-bit state for the odd-sample all-pass section.

The two fixed coefficients are signed 16-bit constants:

- `A_fb1_20 = 10788`
- `A_fb1_21 = -24290`

Internal state and intermediate all-pass outputs use Q10 scaling relative to the original PCM samples. A signed 16-bit sample `x` is promoted to signed 32-bit Q10 as `x << 10`.

For each accepted input transaction, the block performs the following steps in the stated order.

1. Even-sample all-pass section

   Let:

   - `in32_0 = sign_extend_32(in_s0) << 10`
   - `Y0 = in32_0 - S[0]`

   Compute:

   - `X0 = Y0 + trunc_toward_negative_infinity((Y0 * A_fb1_21) / 65536)`

   where the division by 65536 is an arithmetic right shift by 16 bits of the signed product.

   Then:

   - `out_1 = S[0] + X0`
   - `S[0] := in32_0 + X0`

2. Odd-sample all-pass section

   Let:

   - `in32_1 = sign_extend_32(in_s1) << 10`
   - `Y1 = in32_1 - S[1]`

   Compute:

   - `X1 = trunc_toward_negative_infinity((Y1 * A_fb1_20) / 65536)`

   where the division by 65536 is an arithmetic right shift by 16 bits of the signed product.

   Then:

   - `out_2 = S[1] + X1`
   - `S[1] := in32_1 + X1`

3. Low-band and high-band outputs

   Form the signed 32-bit Q10 sums:

   - `sum_L = out_2 + out_1`
   - `sum_H = out_2 - out_1`

   Convert each Q10 quantity back to signed 16-bit PCM by rounded arithmetic right shift by 11 bits, then saturate to the signed 16-bit range.

   Define rounded arithmetic right shift by 11 as:

   `rshift_round_11(v) = ((v >>> 10) + 1) >>> 1`, where `>>>` is
   arithmetic right shift (floor division by a power of two). This rounds
   to nearest, with ties toward positive infinity: `1024` becomes `1`
   and `-1024` becomes `0`.

   Then:

   - `low_tmp  = rshift_round_11(sum_L)`
   - `high_tmp = rshift_round_11(sum_H)`

   Saturation to signed 16-bit is:

   - if `value > 32767`, result is `32767`
   - else if `value < -32768`, result is `-32768`
   - else result is `value`

   The outputs are:

   - `out_outL = low_tmp` saturated to signed 16-bit, returned as a 16-bit two's-complement bit pattern
   - `out_outH = high_tmp` saturated to signed 16-bit, returned as a 16-bit two's-complement bit pattern

The block produces exactly one output transaction for each accepted input transaction.
Input and output transfers occur when their respective valid and ready signals
are both high. An unaccepted output remains valid with unchanged payload;
state changes only on accepted input or reset.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:

- `S[0] = 0`
- `S[1] = 0`

Output ports have no architectural storage. When no output transaction is being presented, `out_valid = 0`. The data values driven on `out_outL` and `out_outH` while `out_valid = 0` are not architecturally significant. After reset, the first accepted input transaction is processed with both state registers equal to zero.
