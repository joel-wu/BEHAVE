# AI ML Audio Mel MFCC DCT

This finite specialization of `create_dct` supports mel counts 1, 2, 4 or 8 and coefficient indices 0 through 7. Values are rounded once to nearest-even binary32 from the stated real formula; mathematical zeros are positive zero. It is matrix construction, not the subsequent MFCC matrix-vector multiplication.

This block computes one column of the MFCC discrete cosine transform matrix used by mel-to-MFCC conversion. For one accepted input transaction, it evaluates the DCT-II basis function at up to 16 mel-bin indices and returns 16 FP32 coefficients in parallel. The algorithm is the DCT construction `cos(pi / n_mels * (n + 0.5) * k)` with either unnormalized scaling (`2.0`) or orthonormal scaling (`sqrt(2/n_mels)` with an additional `1/sqrt(2)` factor for `k = 0`), matching the standard MFCC transform matrix layout `(n_mels, n_mfcc)`.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_mel_mfcc_dct (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_n_mfcc,
    input  logic [7:0]  in_n_mels,
    input  logic [7:0]  in_k,
    input  logic        in_norm_ortho,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_coeff_0,
    output logic [31:0] out_coeff_1,
    output logic [31:0] out_coeff_2,
    output logic [31:0] out_coeff_3,
    output logic [31:0] out_coeff_4,
    output logic [31:0] out_coeff_5,
    output logic [31:0] out_coeff_6,
    output logic [31:0] out_coeff_7,
    output logic [31:0] out_coeff_8,
    output logic [31:0] out_coeff_9,
    output logic [31:0] out_coeff_10,
    output logic [31:0] out_coeff_11,
    output logic [31:0] out_coeff_12,
    output logic [31:0] out_coeff_13,
    output logic [31:0] out_coeff_14,
    output logic [31:0] out_coeff_15
);
```

### Behavior interface

```python
class AiMlAudioMelMfccDct:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_n_mfcc, in_n_mels, in_k, in_norm_ortho):
        pass
```

Each `process` call consumes one accepted input transaction, not a clock cycle.
The three count/index arguments are unsigned 8-bit integers and
`in_norm_ortho` is 0 or 1. It always returns one dictionary with exactly
`out_coeff_0`, `out_coeff_1`, `out_coeff_2`, `out_coeff_3`, `out_coeff_4`,
`out_coeff_5`, `out_coeff_6`, `out_coeff_7`, `out_coeff_8`, `out_coeff_9`,
`out_coeff_10`, `out_coeff_11`, `out_coeff_12`, `out_coeff_13`, `out_coeff_14`
and `out_coeff_15`. Values are unsigned 32-bit FP32 encodings, not Python
floats. Zeroed or invalid columns still return all sixteen fields; the result
is never `None` or a list. Calls have no persistent mathematical state.
Construction and `reset()` produce no output record; `reset()` returns `None`.

## Modules

- `ai_ml_audio_mel_mfcc_dct`: top-level module that computes one DCT column across mel indices 0 through 15.

## Functional Behavior

Each accepted input transaction produces exactly one output transaction.

Inputs:
- `in_n_mfcc` is an unsigned 8-bit integer giving the number of retained MFCC coefficients.
- `in_n_mels` is an unsigned 8-bit integer giving the number of mel filterbank bins.
- `in_k` is an unsigned 8-bit integer selecting the DCT coefficient index.
- `in_norm_ortho` selects normalization: `0` means `norm=None`, `1` means `norm="ortho"`.

Outputs:
- `out_coeff_i` for `i = 0..15` is the raw IEEE-754 FP32 bit pattern of the DCT matrix element at mel-bin index `n = i` and coefficient index `k = in_k`.

For each output lane `i` with `n = i`, compute:

1. Validity gating:
   - If `in_n_mels` is not one of `{1, 2, 4, 8}`, then `out_coeff_i = 32'h00000000`.
   - Else if `in_k >= 8`, then `out_coeff_i = 32'h00000000`.
   - Else if `in_n_mels == 0`, then `out_coeff_i = 32'h00000000`.
   - Else if `n >= in_n_mels`, then `out_coeff_i = 32'h00000000`.
   - Else if `in_k >= in_n_mfcc`, then `out_coeff_i = 32'h00000000`.
   - Otherwise compute the coefficient below.

2. Base cosine:
   - Let
     `angle = pi / in_n_mels * (n + 0.5) * in_k`.
   - Let
     `c = cos(angle)`.

3. Scaling:
   - If `in_norm_ortho == 0`, the coefficient value is
     `v = 2.0 * c`.
   - If `in_norm_ortho == 1`, first apply the `k == 0` adjustment:
     - if `in_k == 0`, replace `c` by `c / sqrt(2.0)`;
     - otherwise leave `c` unchanged.
     Then scale as
     `v = c * sqrt(2.0 / in_n_mels)`.

4. Output encoding:
   - `out_coeff_i` is the IEEE-754 binary32 encoding of real value `v`.
   - `+0.0` is encoded as `32'h00000000`.
   - `-0.0` is not used; any mathematically zero result is encoded as `+0.0`.

The full DCT matrix has shape `(in_n_mels, in_n_mfcc)`. Right-multiplying a mel row vector by this matrix gives its MFCC coefficients. This block returns the matrix column for the selected `k`, padded with zeros to 16 entries.

The legal output range is bounded by the scaling above:
- unnormalized mode: `-2.0 <= v <= 2.0`;
- orthonormal mode: `-|sqrt(2.0 / in_n_mels)| <= v <= |sqrt(2.0 / in_n_mels)|`, except `k = 0` which uses `1 / sqrt(in_n_mels)`.

Worked examples:
- If `in_n_mfcc = 13`, `in_n_mels = 4`, `in_k = 0`, `in_norm_ortho = 0`, then for every valid `n < 4`, `angle = 0`, `cos(angle) = 1`, so `out_coeff_0..out_coeff_3 = 2.0 = 32'h40000000`, and `out_coeff_4..out_coeff_15 = 0`.
- If `in_n_mfcc = 13`, `in_n_mels = 4`, `in_k = 0`, `in_norm_ortho = 1`, then for every valid `n < 4`, `v = (1 / sqrt(2)) * sqrt(2 / 4) = 0.5`, so `out_coeff_0..out_coeff_3 = 32'h3F000000`, and `out_coeff_4..out_coeff_15 = 0`.
- If `in_n_mfcc = 13`, `in_n_mels = 4`, `in_k = 1`, `in_norm_ortho = 0`, then
  `out_coeff_0 = float32(2*cos(pi/8))`,
  `out_coeff_1 = float32(2*cos(3pi/8))`,
  `out_coeff_2 = float32(2*cos(5pi/8))`,
  `out_coeff_3 = float32(2*cos(7pi/8))`,
  and all higher lanes are zero.

The block has no internal memory and no dependence between transactions.

## Reset Behavior

Reset is synchronous active-high via `rst`.

On any cycle with `rst == 1`:
- `out_valid` is deasserted.
- `in_ready` may be asserted or deasserted according to the implementation’s handshake state, but after reset release the block shall be able to accept a new input transaction.
- All output data registers, if implemented, are set to `32'h00000000`:
  - `out_coeff_0` through `out_coeff_15` = `32'h00000000`.
- Any internal busy or pending-output state is cleared to zero.

There is no other architectural state.
