# RNN-T blank merging and nonblank beam expansion

Perform one bounded RNN-T inner-search iteration, returning blank-completed hypotheses and nonblank active proposals separately; this is not a complete speech decoder.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_rnnt_rnnt_beam_decode (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [3:0] in_beam_width,
    input logic [3:0] in_vocab_size,
    input logic [2:0] in_blank,
    input logic [3:0] in_num_hypos,
    input logic [2047:0] in_tokens_flat,
    input logic [47:0] in_lengths,
    input logic [255:0] in_scores_flat,
    input logic [2047:0] in_probs_flat,
    input logic out_ready,
    output logic out_valid,
    output logic [3:0] out_a_count,
    output logic [2047:0] out_a_tokens,
    output logic [47:0] out_a_lengths,
    output logic [511:0] out_a_scores,
    output logic [3:0] out_b_count,
    output logic [2047:0] out_b_tokens,
    output logic [47:0] out_b_lengths,
    output logic [511:0] out_b_scores,
    output logic [0:0] out_error
);
```

### Behavior interface

```python
class AiMlAudioRnntRnntBeamDecode:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_beam_width, in_vocab_size, in_blank, in_num_hypos, in_tokens_flat, in_lengths, in_scores_flat, in_probs_flat): ...
```

Each call represents one accepted request and returns one dictionary with
exactly these keys and unsigned bit widths:

| Key | Bits |
| --- | ---: |
| `out_a_count`, `out_b_count` | 4 each |
| `out_a_tokens`, `out_b_tokens` | 2048 each |
| `out_a_lengths`, `out_b_lengths` | 48 each |
| `out_a_scores`, `out_b_scores` | 512 each |
| `out_error` | 1 |

Arguments and fields are nonnegative packed integers, not Python token lists
or floating-point scores. Signed lanes use the encodings below. Both frontiers
are present even when empty; invalid requests also return the complete record.
The return is never `None` or a list. Requests retain no model state;
`reset()` returns `None` and produces no output record.

## Modules

One initial inner-search iteration from torchaudio RNNTBeamSearch: blank extension and duplicate merging (_gen_b_hypos with an initially empty completed list), followed by nonblank top-k and strict completed-beam thresholding (_compute_updated_scores/_gen_a_hypos). It returns the completed B frontier and the still-active A frontier separately. It does not run transcription/prediction networks, subsequent symbol iterations, or final length-normalized ranking. In particular, active proposals are not final completed hypotheses. The historical identifier does not imply a full speech decoder.

## Functional Behavior

1 <= in_beam_width <= 8, 1 <= in_vocab_size <= 8, in_blank=in_vocab_size-1 and 0 <= in_num_hypos <= 8. Other values produce out_error=1 and all other fields zero. Each active input hypothesis has a six-bit length from in_lengths at bit 6*h, at most 31, and a private 256-bit token slot at bit 256*h. Its first length little-endian bytes are its token sequence; unused bytes and inactive slots are ignored. Existing tokens are opaque eight-bit identities. Lengths above 31 are invalid, leaving room to append one symbol without dropping any token.

Scores are signed 32-bit Q16.16 lanes h in in_scores_flat. in_probs_flat holds 8x8 signed Q16.16 log-score increments, lane 8*h+token. They are supplied network outputs, not probabilities to be softmaxed here; the integer extension defines positive increments too. For in_num_hypos=0, initialize one hypothesis [in_blank] with score zero and use probability row zero, as the source initializer does. No neural predictor state is claimed.

For each hypothesis in input order, add its blank increment to its score in a wide integer. Equal token sequences (length and every token) merge by L(a,b)=max(a,b)+P(-abs(a-b)); a difference >=16*65536 uses zero correction. P is the nonnegative rounded softplus correction defined below. Keep distinct B hypotheses in increasing merged-score order, breaking ties by first appearance. No blank token is appended to B. Up to eight B entries are returned.

Separately enumerate every nonblank candidate (h,t), t=0..in_vocab_size-2, with score=score[h]+increment[h,t]. Select at most beam_width candidates by decreasing score, breaking ties by smaller h then t. If B has at least beam_width entries, retain only candidates strictly above its beam_width-th largest score; otherwise retain all selected candidates. Append t to each retained parent's sequence and return this A frontier in selection order. A and B remain separate; there is no final ranking of their union.

Each output frontier has its own count, eight 256-bit token slots, eight six-bit lengths, and eight signed 64-bit Q48.16 score lanes. Entries after count and bytes after each length are zero. Wide score sums and merges do not wrap; the finite input range and at most eight merges fit signed 64 bits.

### Log-add correction

For 0 <= d < 16*65536, compute P(-d) as follows. R(n,d) is signed division rounded to nearest integer, ties even. Q=2^40, L=762123384786. Set v=-d*2^24, k=floor(v/L), r=v-k*L. Start term=total=Q; for j=1..12 set term=R(term*r,Q*j), total=total+term. Set e=total if k=0, otherwise R(total,2^(-k)). Then z=R(e*Q,2*Q+e), z2=R(z*z,Q), term=total=z. For j=1..11 set term=R(term*z2,Q), total=total+R(term,2*j+1). Return R(2*total,2^24). Every intermediate is exact until an explicit R. This approximates log(1+exp(-d/65536))*65536; it is not a coarse integer-score bucket. The cutoff's true correction is below half an output quantum.

## Reset Behavior

Synchronous active-high reset cancels pending output, clears out_valid and all output fields, and leaves in_ready=1. There is no numerical state between transactions. Transfers occur on rising edges with valid and ready high; output valid/payload hold under backpressure. Idle output payloads are zero.
