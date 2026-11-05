# CtcGreedy8x4Int16

Decode a finite CTC best path: select one label per frame, collapse adjacent repeats, and then remove blanks.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_ctc_ctc_greedy_decode (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_scores,
    input logic [3:0] in_length,
    input logic [1:0] in_blank,
    output logic out_valid,
    input logic out_ready,
    output logic [3:0] out_count,
    output logic [15:0] out_tokens,
    output logic [23:0] out_times
);
endmodule
```

Input and output transfers occur on rising clock edges when their respective
`valid` and `ready` signals are both high and reset is inactive. Each accepted
input produces exactly one output, in acceptance order. A stalled output must
retain its valid bit and payload until accepted or reset. Latency and internal
architecture are not prescribed. The input producer likewise holds a stalled
request stable.

### Behavior interface

```python
class CtcGreedy8x4Int16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_scores, in_length, in_blank): ...
```

Arguments are unsigned port bit patterns. Each call represents one accepted
request and returns exactly one dictionary with keys `out_count`, `out_tokens`
and `out_times`, carrying unsigned 4-, 16- and 24-bit port words respectively.
Labels and frame indices are packed into these words, not returned as lists.
An empty or all-blank path still returns all three keys with zero values, not
`None`. No clock or backpressure calls are needed. There is no cross-request
state; `reset()` returns `None` and emits no response.

## Modules

`ai_ml_audio_ctc_ctc_greedy_decode` is the sole top-level module.

## Functional Behavior

The request holds up to eight time frames and four labels per frame.
`in_scores[16*(4*t+k) +: 16]` is the signed two's-complement score for frame
`t` and label `k`. A common positive scale may be used for all scores; only
their ordering matters. Scores need not be probabilities. All bit patterns
are legal. `in_blank` selects any one of labels 0 through 3 as blank.

Use `T = min(in_length, 8)` frames; the rest of the packed scores are ignored.
At each frame `t < T`, choose `a[t]`, the smallest label index with maximal
signed score. Keep the first label of each consecutive run of equal `a[t]`,
then remove runs whose label equals `in_blank`. A blank separates equal
nonblank labels: `[1,1,blank,1]` therefore yields `[1,1]`, not `[1]`.

`out_count` is the number of retained labels, from 0 through 8. Retained label
`j` occupies `out_tokens[2*j +: 2]`; its original run-start frame index occupies
`out_times[3*j +: 3]`. Entries are chronological. All unused entries are zero.
An empty input (`in_length=0`) or all-blank path returns a response with all
three output fields zero. Length codes 9 through 15 mean eight frames.

For example, with blank 0 and winning labels `[2,2,0,2,3,3,0,1]`, the output
labels are `[2,2,3,1]` at frames `[0,3,4,7]`. There is no beam search, language
model, vocabulary-to-text conversion or state carried between requests.

## Reset Behavior

Reset is active-high and synchronous to the rising clock edge. Reset clears `out_valid` and all output payloads to zero and makes
`in_ready` high. Any outstanding response is discarded. Requests are not
accepted during reset. The computation has no state between transactions.
