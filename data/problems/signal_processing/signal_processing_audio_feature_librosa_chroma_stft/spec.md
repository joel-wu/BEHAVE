# ChromaSTFT

ChromaSTFT computes one chroma vector from one input STFT power-spectrum frame. The block multiplies the input spectrum by a fixed chroma filter-bank matrix and then applies per-frame column normalization across chroma bins. The algorithm is the direct frame-wise form of `chroma_stft`: `raw_chroma = chromafb × S`, followed by normalization along the chroma axis. The filter-bank coefficients are supplied explicitly on the input interface so the block does not construct filters internally.

## Interface

### RTL interface

```systemverilog
module chroma_stft #(
    parameter int N_FREQ   = 5,
    parameter int N_CHROMA = 12,
    parameter int W_IN     = 32,
    parameter int W_COEF   = 32,
    parameter int W_OUT    = 32
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic [N_FREQ*W_IN-1:0]           in_s,
    input  logic [N_CHROMA*N_FREQ*W_COEF-1:0] in_chromafb,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic [N_CHROMA*W_OUT-1:0]        out_chroma
);
```

`in_s` packs `N_FREQ` unsigned `W_IN`-bit spectrum bins in ascending frequency order, least-significant packed slice first:
- bin `f` is `in_s[f*W_IN +: W_IN]`.

`in_chromafb` packs `N_CHROMA × N_FREQ` unsigned `W_COEF`-bit filter coefficients in chroma-major, then frequency-minor order, least-significant packed slice first:
- coefficient `(c,f)` is `in_chromafb[(c*N_FREQ + f)*W_COEF +: W_COEF]`.

`out_chroma` packs `N_CHROMA` unsigned `W_OUT`-bit normalized chroma bins, chroma index order, least-significant packed slice first:
- chroma bin `c` is `out_chroma[c*W_OUT +: W_OUT]`.

Arithmetic interpretation:
- `in_s` values are unsigned Q16.16.
- `in_chromafb` values are unsigned Q16.16.
- `out_chroma` values are unsigned Q16.16.

### Behavior interface

```python
class ChromaSTFT:
    def __init__(self, n_freq=5, n_chroma=12, w_in=32, w_coef=32, w_out=32): ...
    def reset(self): ...
    def process(self, in_s, in_chromafb): ...
```

Python configuration arguments correspond to the uppercase RTL parameters.
Each `process(in_s, in_chromafb)` call represents one accepted frame.
Arguments are unsigned packed integers of the declared total port widths,
not arrays or floating-point values. Return one dictionary with exactly
the key `out_chroma`:

```python
{"out_chroma": ...}
```

The value is an unsigned `n_chroma*w_out`-bit packed integer, not a list of
chroma bins. A zero-energy frame still returns this dictionary with value
zero, not `None`. Frames are independent. `reset()` returns `None` and
produces no response.

## Modules

- `chroma_stft`: top-level module that computes one normalized chroma vector from one packed spectrum frame and one packed chroma filter-bank matrix.

## Functional Behavior

Dimensions and input widths must be positive; `W_OUT >= 17` holds the encoded
value 1.0. Each numeric format has 16 fractional bits, including when a width
other than 32 is selected.

For each accepted input transaction:

1. Unpack the input spectrum:
   - For each frequency index `f` in `[0, N_FREQ-1]`, decode
     `S[f] = unsigned(in_s[f*W_IN +: W_IN])`.
   - `S[f]` represents Q16.16, with real value `S[f] * 2^-16`.

2. Unpack the chroma filter bank:
   - For each chroma index `c` in `[0, N_CHROMA-1]` and frequency index `f` in `[0, N_FREQ-1]`, decode
     `F[c,f] = unsigned(in_chromafb[(c*N_FREQ + f)*W_COEF +: W_COEF])`.
   - `F[c,f]` represents Q16.16, with real value `F[c,f] * 2^-16`.

3. Compute raw chroma energies by matrix-vector multiplication in chroma order:
   - For each chroma bin `c`,
     `raw[c] = sum_{f=0..N_FREQ-1} (S[f] * F[c,f])`.
   - Each product is formed as unsigned Q32.32.
   - The accumulation is exact over all `N_FREQ` terms in an unsigned accumulator wide enough to avoid overflow for the configured parameters.
   - Keep every accumulated bit through normalization. There is no intermediate
     conversion to Q16.16: even a product smaller than one output unit can
     contribute to a nonzero normalized bin.

4. Compute the normalization denominator across chroma bins according to `norm = +inf`:
   - `denom = max(raw[0], raw[1], ..., raw[N_CHROMA-1])`.
   - This is the maximum raw chroma value in the frame.

5. Normalize each chroma bin:
   - If `denom == 0`, then every output chroma bin is `0`.
   - Otherwise, for each chroma bin `c`,
     `normed[c] = floor((raw[c] << 16) / denom)`.
   - `normed[c]` is unsigned Q16.16.
   - Because `raw[c] <= denom`, each `normed[c]` lies in `[0x00000000, 0x00010000]`, corresponding to real range `[0.0, 1.0]`.
   - Division is exact integer division with truncation toward zero.

6. Pack the outputs:
   - `out_chroma[c*W_OUT +: W_OUT] = normed[c]` for each `c` in `[0, N_CHROMA-1]`.

Operation order is fixed:
- unpack spectrum,
- unpack filter bank,
- compute all raw chroma bins,
- compute `denom` as the maximum of the raw chroma bins,
- normalize all bins with the same `denom`,
- pack outputs.

Edge cases:
- If every `raw[c]` is zero, output all zeros.
- If multiple bins equal the maximum, each such bin normalizes to exactly `0x00010000`.
- No saturation is applied beyond the natural bound from `raw[c] <= denom`.
- Negative values are not representable on any port.

## Reset Behavior

Reset is synchronous active-high.

On a reset cycle:
- `out_valid = 0`
- `out_chroma = 0`
- the block holds no architectural state affecting future numerical results

After reset deassertion, each accepted input produces one independent output.
Transfers require valid and ready; unaccepted outputs hold data and valid.
Reset cancels any pending output.
