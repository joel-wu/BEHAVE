# FirInterpolatorQ15L4

FirInterpolatorQ15L4 is a Q15 polyphase FIR interpolator with fixed interpolation factor `L = 4` and fixed polyphase length `phase_length = 8`. Each accepted input sample is appended to an internal state history and produces four output samples, one for each interpolation phase. The computation uses signed 16-bit 1.15 input samples and coefficients, forms signed 2.30 products, accumulates them in a signed 64-bit accumulator with no internal saturation, then arithmetic-right-shifts by 15 bits and saturates the result to signed 16-bit 1.15.

## Interface

### RTL interface

```systemverilog
module fir_interpolator_q15_l4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);
```

### Behavior interface

```python
class FirInterpolatorQ15L4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_sample):
        pass
```

The `process()` return value is a list of four dictionaries in output order. Each dictionary has one key: `out_sample`.

Return a list of exactly four dictionaries in the specified polyphase output order for
every accepted input sample. Never return None or a bare dictionary. Filter history
persists across calls and is cleared by reset.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fir_interpolator_q15_l4`: top-level interpolating FIR kernel with internal coefficient ROM and state buffer.

## Functional Behavior

Constants are fixed for this kernel:

- Interpolation factor `L = 4`.
- Polyphase length `phase_length = 8`.
- Number of coefficients `num_taps = L * phase_length = 32`.

`in_sample` and `out_sample` are 16-bit two's-complement signed Q15 values carried on the ports as raw 16-bit bit patterns.

The coefficient array is a fixed 32-entry Q15 ROM named `coeffs[0..31]`, indexed in the same order as the CMSIS-DSP interpolator coefficient buffer. The exact coefficient values are:

- `coeffs[0]  = 0x0100`
- `coeffs[1]  = 0x0200`
- `coeffs[2]  = 0x0300`
- `coeffs[3]  = 0x0400`
- `coeffs[4]  = 0x0500`
- `coeffs[5]  = 0x0600`
- `coeffs[6]  = 0x0700`
- `coeffs[7]  = 0x0800`
- `coeffs[8]  = 0x0900`
- `coeffs[9]  = 0x0A00`
- `coeffs[10] = 0x0B00`
- `coeffs[11] = 0x0C00`
- `coeffs[12] = 0x0D00`
- `coeffs[13] = 0x0E00`
- `coeffs[14] = 0x0F00`
- `coeffs[15] = 0x1000`
- `coeffs[16] = 0x1100`
- `coeffs[17] = 0x1200`
- `coeffs[18] = 0x1300`
- `coeffs[19] = 0x1400`
- `coeffs[20] = 0x1500`
- `coeffs[21] = 0x1600`
- `coeffs[22] = 0x1700`
- `coeffs[23] = 0x1800`
- `coeffs[24] = 0x1900`
- `coeffs[25] = 0x1A00`
- `coeffs[26] = 0x1B00`
- `coeffs[27] = 0x1C00`
- `coeffs[28] = 0x1D00`
- `coeffs[29] = 0x1E00`
- `coeffs[30] = 0x1F00`
- `coeffs[31] = 0x2000`

The architectural state is an 8-entry signed Q15 sample history `state[0..7]`. `state[0]` is the oldest sample participating in the next output computation and `state[7]` is the most recently accepted sample.

For each accepted input transaction:

1. Interpret `in_sample` as a signed 16-bit integer.
2. Shift the state history left by one sample:
   - new `state[0] = old state[1]`
   - new `state[1] = old state[2]`
   - new `state[2] = old state[3]`
   - new `state[3] = old state[4]`
   - new `state[4] = old state[5]`
   - new `state[5] = old state[6]`
   - new `state[6] = old state[7]`
   - new `state[7] = signed(in_sample)`
3. Compute four output samples, in phase order `phase = 0, 1, 2, 3`. For each phase:
   - Initialize signed 64-bit accumulator `sum = 0`.
   - For tap index `k = 0..7`, accumulate
     - `sum += state[k] * coeffs[(3 - phase) + 4*k]`
   - Each multiplication is signed 16-bit by signed 16-bit, producing an exact signed product.
   - The accumulation order is strictly increasing `k` from 0 to 7.
   - After the last addition, compute `shifted = sum >> 15` using arithmetic right shift.
   - Saturate `shifted` to the signed 16-bit range `[-32768, 32767]`.
   - Return the saturated value on `out_sample` as a 16-bit unsigned bit pattern.

The four returned outputs correspond exactly to the scalar CMSIS-DSP loop with address modifier `j = 1, 2, 3, 4` and coefficient base pointer `pCoeffs + (L - j)`. Written explicitly:

- Phase 0 output uses coefficients `coeffs[3], coeffs[7], coeffs[11], coeffs[15], coeffs[19], coeffs[23], coeffs[27], coeffs[31]`.
- Phase 1 output uses coefficients `coeffs[2], coeffs[6], coeffs[10], coeffs[14], coeffs[18], coeffs[22], coeffs[26], coeffs[30]`.
- Phase 2 output uses coefficients `coeffs[1], coeffs[5], coeffs[9], coeffs[13], coeffs[17], coeffs[21], coeffs[25], coeffs[29]`.
- Phase 3 output uses coefficients `coeffs[0], coeffs[4], coeffs[8], coeffs[12], coeffs[16], coeffs[20], coeffs[24], coeffs[28]`.

This is a 1:N block: one accepted input sample produces four output transactions in the above order.

Edge cases:
- Negative values use two's-complement signed arithmetic throughout.
- The accumulator does not saturate before the final shift.
- The right shift discards the low 15 bits; no rounding is applied.
- Final saturation clamps values greater than `32767` to `32767` and values less than `-32768` to `-32768`.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:

- `in_ready = 1`.
- `out_valid = 0`.
- `out_sample = 16'h0000`.
- `state[0..7] = 0`.
- No output transactions are pending.
