# AiMlTxKvCacheKvCacheAppend

A finite, per-layer specialization of transformer `DynamicLayer.update`: concatenate new key/value tokens along the sequence axis and return the entire updated cache. Historical K/V data are observable, not merely stored internally. Capacity is explicitly bounded for hardware; a full cache reports a rejected append without overwriting history.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_kv_cache_kv_cache_append #(
    parameter int NUM_LAYERS=4,
    parameter int BATCH_SIZE=1,
    parameter int NUM_HEADS=1,
    parameter int HEAD_DIM=8,
    parameter int TOKEN_BLOCK=1,
    parameter int DATA_WIDTH=16,
    parameter int MAX_TOKENS=16,
    parameter int LAYER_IDX_WIDTH=(NUM_LAYERS<=1)?1:$clog2(NUM_LAYERS),
    parameter int SEQ_LEN_WIDTH=16
) (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [LAYER_IDX_WIDTH-1:0] in_layer_idx,
    input logic [BATCH_SIZE*NUM_HEADS*TOKEN_BLOCK*HEAD_DIM*DATA_WIDTH-1:0] in_key_states,
    input logic [BATCH_SIZE*NUM_HEADS*TOKEN_BLOCK*HEAD_DIM*DATA_WIDTH-1:0] in_value_states,
    output logic out_valid,
    input logic out_ready,
    output logic out_append_ok,
    output logic [SEQ_LEN_WIDTH-1:0] out_seq_length,
    output logic [BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH-1:0] out_cached_key_states,
    output logic [BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH-1:0] out_cached_value_states
);
```

### Behavior interface

```python
class AiMlTxKvCacheKvCacheAppend:
    def __init__(self, num_layers=4, batch_size=1, num_heads=1, head_dim=8,
                 token_block=1, data_width=16, seq_len_width=16, max_tokens=16): pass
    def reset(self): pass
    def process(self, in_layer_idx, in_key_states, in_value_states): pass
```

Constructor parameters correspond to the uppercase RTL parameters.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_append_ok`: 1.
- `out_cached_key_states`, `out_cached_value_states`:
  BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH.
- `out_seq_length`: SEQ_LEN_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Parameter domain and protocol

LAYER_IDX_WIDTH is a transport width, not a separate cache dimension. Dimensions are positive; TOKEN_BLOCK<=MAX_TOKENS and MAX_TOKENS<2^SEQ_LEN_WIDTH. LAYER_IDX_WIDTH must address all layers.

One accepted input produces one ordered output record with `out_append_ok`, `out_seq_length`, `out_cached_key_states`, `out_cached_value_states`. Ready/valid transfer is sampled at rising edges; unconsumed output valid and all payload words remain stable under backpressure. An accepted request that cannot append still produces a response, with `out_append_ok=0`; transport acceptance and cache insertion are distinct.

## Modules

The named top module holds all layers' K/V caches. No external memory, hidden readback channel or learned arithmetic is used.

## Functional Behavior

Input K/V have logical shape [BATCH_SIZE, NUM_HEADS, TOKEN_BLOCK, HEAD_DIM]. Output K/V have padded shape [BATCH_SIZE, NUM_HEADS, MAX_TOKENS, HEAD_DIM]. Elements are opaque DATA_WIDTH-bit words. In both shapes, row-major flattened element zero occupies the least-significant DATA_WIDTH bits. In particular, input element [b,h,t,d] starts at DATA_WIDTH*(((b*NUM_HEADS+h)*TOKEN_BLOCK+t)*HEAD_DIM+d); output element [b,h,t,d] starts at DATA_WIDTH*(((b*NUM_HEADS+h)*MAX_TOKENS+t)*HEAD_DIM+d).

Each layer l owns an independent token count n[l] and zero-padded K/V arrays. Initially every count and word is zero. On each accepted request:

1. Select l=in_layer_idx modulo NUM_LAYERS. This is the defined finite-port policy for unused index encodings.
2. If n[l]+TOKEN_BLOCK<=MAX_TOKENS, copy every incoming [b,h,t,d] into cache [b,h,n[l]+t,d], increment n[l] by TOKEN_BLOCK, and set out_append_ok=1.
3. Otherwise leave that layer's count and all cached data unchanged and set out_append_ok=0. No partial insertion, wraparound, eviction or silent truncation occurs.
4. Return the selected layer's current count and complete padded K/V arrays, after any successful append. Tokens below the count are in insertion order; unused positions remain zero. Other layers are unchanged.

The sequence length counts tokens, not input transactions: TOKEN_BLOCK=2 increases it by two. For each batch/head slice, the valid prefix is exactly concatenation of its accepted token blocks, matching the source sequence-axis update. Returning a padded tensor, fixing finite capacity and defining full-cache rejection are explicit hardware adaptations of the dynamically allocated source API. The task does not claim unbounded growth. Historical words influence later returned caches, so an implementation that only counts and echoes the newest block is incorrect.

## Reset Behavior

Synchronous active-high rst zeroes all layers' counts and K/V arrays, discards pending output, and clears out_valid and all output payloads; in_ready=1 after reset. Idle output payloads are zero. State changes only on reset or an accepted successful append. After reset, the next request observes empty history.
