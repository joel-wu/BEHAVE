# Spectral Rolloff

This block computes the spectral rolloff frequency for one spectrogram frame per accepted input transaction. The input frame is a fixed set of 16 spectral magnitudes and 16 corresponding frequency codes. The block forms the cumulative sum of magnitudes from bin 0 upward and returns the frequency code of the first bin whose cumulative sum reaches or exceeds a programmable fraction of the total frame energy. Each accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module spectral_rolloff (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_roll_percent,

    input  logic [31:0]  in_S_0,
    input  logic [31:0]  in_S_1,
    input  logic [31:0]  in_S_2,
    input  logic [31:0]  in_S_3,
    input  logic [31:0]  in_S_4,
    input  logic [31:0]  in_S_5,
    input  logic [31:0]  in_S_6,
    input  logic [31:0]  in_S_7,
    input  logic [31:0]  in_S_8,
    input  logic [31:0]  in_S_9,
    input  logic [31:0]  in_S_10,
    input  logic [31:0]  in_S_11,
    input  logic [31:0]  in_S_12,
    input  logic [31:0]  in_S_13,
    input  logic [31:0]  in_S_14,
    input  logic [31:0]  in_S_15,

    input  logic [31:0]  in_freq_0,
    input  logic [31:0]  in_freq_1,
    input  logic [31:0]  in_freq_2,
    input  logic [31:0]  in_freq_3,
    input  logic [31:0]  in_freq_4,
    input  logic [31:0]  in_freq_5,
    input  logic [31:0]  in_freq_6,
    input  logic [31:0]  in_freq_7,
    input  logic [31:0]  in_freq_8,
    input  logic [31:0]  in_freq_9,
    input  logic [31:0]  in_freq_10,
    input  logic [31:0]  in_freq_11,
    input  logic [31:0]  in_freq_12,
    input  logic [31:0]  in_freq_13,
    input  logic [31:0]  in_freq_14,
    input  logic [31:0]  in_freq_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_rolloff
);
```

### Behavior interface

```python
class SpectralRolloff:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_roll_percent,
        in_S_0, in_S_1, in_S_2, in_S_3,
        in_S_4, in_S_5, in_S_6, in_S_7,
        in_S_8, in_S_9, in_S_10, in_S_11,
        in_S_12, in_S_13, in_S_14, in_S_15,
        in_freq_0, in_freq_1, in_freq_2, in_freq_3,
        in_freq_4, in_freq_5, in_freq_6, in_freq_7,
        in_freq_8, in_freq_9, in_freq_10, in_freq_11,
        in_freq_12, in_freq_13, in_freq_14, in_freq_15
    ):
        pass
```

Each `process(...)` call represents one accepted frame and returns exactly
`{"out_rolloff": frequency_code}`, with `frequency_code` an unsigned 32-bit
integer. The spectrum and frequency arguments are separate unsigned 32-bit
integers; `in_roll_percent` is an unsigned 16-bit integer encoding UQ0.16,
not a Python float. Every frame, including a zero spectrum or zero fraction,
returns one dictionary, never `None` or a list. No frame history persists;
`reset()` returns `None` and emits no result.

## Modules

- `spectral_rolloff`: top-level module that computes one rolloff frequency code from one 16-bin input frame.

## Functional Behavior

The block uses exactly 16 bins. All input and output ports are unsigned integer bit patterns.

`in_roll_percent` is an unsigned 16-bit fixed-point fraction in UQ0.16 format. Its mathematical value is `in_roll_percent / 65536`. Example: `0x8000` represents `0.5`. All 65536 bit patterns are defined: zero selects bin 0, and the largest value represents `65535/65536`, not 1.

`in_S_0` through `in_S_15` are 16 unsigned 32-bit spectral magnitudes.

`in_freq_0` through `in_freq_15` are 16 unsigned 32-bit frequency codes associated with the 16 spectral bins. The computation uses the bins in index order 0 through 15 exactly as presented on the ports. The block does not sort frequencies and does not inspect frequency monotonicity.

With nondecreasing frequency codes and a nonzero fraction, this is the
fixed-16-bin, integer-magnitude form of spectral rolloff. Zero fraction and
arbitrary frequency-code ordering are explicit task extensions: even for an
unsorted code vector, the first qualifying **bin index**, not the minimum
qualifying frequency code, determines the answer. No STFT or magnitude-squaring
operation is part of this block.

For one accepted input transaction, define:

- `S[0] = in_S_0`, `S[1] = in_S_1`, ..., `S[15] = in_S_15`
- `F[0] = in_freq_0`, `F[1] = in_freq_1`, ..., `F[15] = in_freq_15`

Then compute:

1. Total energy:
   `total_energy = S[0] + S[1] + ... + S[15]`

2. Threshold numerator:
   `threshold_num = in_roll_percent * total_energy`

3. Cumulative energies:
   - `cumulative[0]  = S[0]`
   - `cumulative[1]  = S[0] + S[1]`
   - `cumulative[2]  = S[0] + S[1] + S[2]`
   - ...
   - `cumulative[15] = S[0] + S[1] + ... + S[15]`

4. Selection rule:
   choose the smallest index `k` in the set `{0,1,...,15}` such that
   `(cumulative[k] << 16) >= threshold_num`

5. Output:
   `out_rolloff = F[k]`

The comparison in step 4 is inclusive: equality qualifies.

All additions, the multiplication, and the left shift in the equations above are mathematical integer operations with unlimited precision in the functional definition. There is no modulo wrap, truncation, rounding, or saturation before the final port value is selected from `F[k]`.

If more than one index satisfies the comparison, the first such index is selected. If no index from 0 through 14 satisfies the comparison, index 15 is selected. For legal inputs with `0 < in_roll_percent < 65536`, index 15 always satisfies the comparison because `cumulative[15] = total_energy`.

Special cases:
- If all sixteen magnitudes are zero, then `total_energy = 0`, `threshold_num = 0`, every cumulative sum is zero, index 0 is selected, and `out_rolloff = in_freq_0`.
- If `S[0]` alone satisfies `(S[0] << 16) >= threshold_num`, then `out_rolloff = in_freq_0`.
- If the first satisfying index is `j`, changing `F[m]` for any `m != j` does not change the output.

Worked example:
- `in_roll_percent = 0x8000`
- `S = [2, 3, 5, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]`
- `F = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600]`

Then:
- `total_energy = 20`
- `threshold_num = 0x8000 * 20`
- `cumulative[0] = 2`, `cumulative[1] = 5`, `cumulative[2] = 10`
- `(2 << 16) < threshold_num`
- `(5 << 16) < threshold_num`
- `(10 << 16) >= threshold_num`
- selected index `k = 2`
- `out_rolloff = 300`

Output cardinality is exactly 1:1: every accepted input transaction produces exactly one output transaction.

Acceptance occurs on a rising edge with `in_valid && in_ready` and reset
inactive; output transfer occurs with `out_valid && out_ready`. Requests and
responses remain stable while stalled, and response order follows acceptance
order. Backpressure and internal latency are implementation choices within
the evaluation policy, not fixed-cycle requirements.

## Reset Behavior

Reset is synchronous active-high on `rst`.

Architectural state after reset:
- no buffered input frame
- no stored partial sum
- no pending output result

Output port values during reset and in the idle state after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_rolloff = 0`

Reset takes priority over transfers and cancels any pending result. Once a
result has transferred, its payload is irrelevant while `out_valid` is low;
it need not be cleared until reset.
