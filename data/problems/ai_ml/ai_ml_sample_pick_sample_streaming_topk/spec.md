# Framed Signed-Score Greedy Argmax

This block selects the maximum-score token ID from a supplied frame of candidate tokens. It does not compute a top-k set or make a random draw. One request is accumulated across multiple accepted input transactions; each transaction supplies one candidate token ID and its associated score together with frame-control flags. When the last candidate of the request is received, the block emits exactly one winning token. The selection algorithm is deterministic argmax over the received candidate scores with a lowest-token-ID tie break, which corresponds to greedy selection within the supplied candidate frame.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_pick_sample_streaming_topk (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic         in_last,
    input  logic [15:0]  in_token_id,
    input  logic [31:0]  in_score,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_token_id
);
```

### Behavior interface

```python
class AiMlSamplePickSampleStreamingTopk:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_start, in_last, in_token_id, in_score): ...
```

`process(...)` returns either `None` for non-final beats or `{"out_token_id": ...}` on the final beat of a request.

Return None when in_last=0. A call with in_last=1 returns exactly one dictionary,
including the specified zero result if no frame is active. Never return a list. Reset
discards the partial frame.
Every returned dictionary contains exactly these fields (bit widths):

- `out_token_id`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_sample_pick_sample_streaming_topk`: top-level module and complete implementation.

## Functional Behavior

The block processes a variable-length frame of candidate entries. Each accepted input transaction carries:

- `in_start`: 1 if this transaction is the first candidate of a new frame.
- `in_last`: 1 if this transaction is the last candidate of the current frame.
- `in_token_id`: unsigned 16-bit candidate token ID.
- `in_score`: signed 32-bit two's-complement candidate score.

A frame is the sequence of accepted input transactions beginning with a beat where `in_start = 1` and ending with the first subsequent beat where `in_last = 1`. The first beat of a frame may also be the last beat of that frame.

Score comparison is signed integer ordering, not unsigned bit-pattern ordering or floating-point decoding. For example, score 1 beats score -1 (0xffffffff).

Architectural state during a frame:

- `best_token_id`: unsigned 16-bit current winning token ID.
- `best_score`: signed 32-bit current winning score.
- `frame_active`: 1 if a frame has been started and not yet completed.

Per accepted input transaction, the behavior is:

1. If `in_start = 1`, the current transaction starts a new frame. The current candidate becomes the initial winner:
   - `best_token_id := in_token_id`
   - `best_score := in_score`
   - `frame_active := 1`

2. Else, the transaction is treated as a continuation beat of the current frame. The candidate is compared against the current winner only if `frame_active = 1`. The candidate replaces the current winner if either of the following is true:
   - `in_score > best_score`
   - `in_score == best_score` and `in_token_id < best_token_id`

   If neither condition is true, the current winner is unchanged.

3. If `in_last = 1`, the frame ends on this transaction. The block emits exactly one output transaction:
   - `out_token_id =` the winning token ID after applying steps 1 and 2 to the current input beat.

4. After a beat with `in_last = 1`, the frame state is cleared:
   - `frame_active := 0`
   - `best_token_id := 0`
   - `best_score := 0`

Additional required behavior:

- A one-beat frame is represented by `in_start = 1` and `in_last = 1` on the same accepted input transaction. The emitted `out_token_id` is that beat's `in_token_id`.
- Tie breaking is fully deterministic: for equal scores, the numerically smaller `in_token_id` wins.
- The block accepts no nested frames. A beat with `in_start = 1` always begins a new frame and discards any previously accumulated winner from an unfinished frame.
- If a beat arrives with `in_start = 0` while `frame_active = 0`, that beat does not compare against prior state. If `in_last = 1` on that same beat, the emitted `out_token_id` is 0 because no active-frame winner exists before frame termination. If `in_last = 0`, the state remains inactive and no output is emitted.
- Output cardinality is N:1 over a frame: zero outputs for each non-final beat, and one output on the final beat.

### Source boundary

The archived vLLM `Sampler.greedy_sample` uses argmax over logits. This task
retains that reduction, with signed integer scores, an explicit candidate frame,
and lowest-token-ID tie breaking independent of arrival order. When token IDs
denote vocabulary positions, that tie rule matches choosing the lowest tied
position. It is not stochastic sampling or a maintained top-k heap. The legacy
directory/module identifier is retained only for dataset identity.

Only accepted input beats change the winner. Hold a pending response stable
under backpressure. Reset discards an unfinished frame and pending response;
an explicit new start also replaces an unfinished frame. The specified orphan
last-beat result of zero is a framing extension, not a sampled category.

## Reset Behavior

Reset is synchronous active-high on `rst`.

On reset:

- `in_ready = 1`
- `out_valid = 0`
- `out_token_id = 0`
- `frame_active = 0`
- `best_token_id = 16'h0000`
- `best_score = 32'h00000000`

After reset is deasserted, the block may accept a new input frame immediately.
