# Integer KV Row Quantizer

Quantize all lanes of one key/value row using independent integer scale divisors and carry the slot tag. This is an integer INT8 adaptation of a cache-write quantization stage, not a paged-cache memory.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_kv_int8_kv_cache_quant #(
    parameter int HEAD_SIZE = 128,
    parameter int SLOT_BITS = 16
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [SLOT_BITS-1:0]     in_slot,
    input  logic [16*HEAD_SIZE-1:0]  in_key,
    input  logic [16*HEAD_SIZE-1:0]  in_value,
    input  logic [31:0]              in_k_scale_num,
    input  logic [31:0]              in_v_scale_num,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [SLOT_BITS-1:0]     out_slot,
    output logic [8*HEAD_SIZE-1:0]   out_key_q,
    output logic [8*HEAD_SIZE-1:0]   out_value_q
);
```

Port interpretation:
- `in_key[i*16 +: 16]` is signed element `i` of the input key vector, for `i = 0 .. HEAD_SIZE-1`.
- `in_value[i*16 +: 16]` is signed element `i` of the input value vector, for `i = 0 .. HEAD_SIZE-1`.
- `in_k_scale_num` and `in_v_scale_num` are unsigned 32-bit integer divisors used for key and value quantization.
- `out_key_q[i*8 +: 8]` and `out_value_q[i*8 +: 8]` are two's-complement INT8 quantized elements.

### Behavior interface

```python
class KVCacheInt8Quant:
    def __init__(self, head_size=128, slot_bits=16):
        pass

    def reset(self):
        pass

    def process(self, in_slot, in_key, in_value, in_k_scale_num, in_v_scale_num):
        pass
```

The returned dict has keys:
- `out_slot`
- `out_key_q`
- `out_value_q`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_key_q`, `out_value_q`: 8*HEAD_SIZE.
- `out_slot`: SLOT_BITS.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor head_size and slot_bits match RTL HEAD_SIZE and SLOT_BITS.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

Parameters `HEAD_SIZE` and `SLOT_BITS` must be positive. Each input row contains HEAD_SIZE signed 16-bit integers, lane i in bits `[16*i +: 16]`; output lanes occupy `[8*i +: 8]`. All key lanes use unsigned divisor `in_k_scale_num`; all value lanes independently use `in_v_scale_num`.

For signed lane x and divisor s, return zero if s=0. Otherwise round x/s to nearest integer, ties away from zero, then **saturate** to [-128,127]. Equivalently, magnitude is `(2*abs(x)+s)//(2*s)` before sign restoration and saturation. Encode the signed result in 8 bits; do not truncate an out-of-range quotient. Intermediates are exact, including abs(-32768) and twice a maximal 32-bit divisor. Forward the slot unchanged, with no signed sentinel semantics. Examples: (7,2)->4, (-7,2)->-4, (128,1)->127, (-129,1)->-128. The block does not allocate slots, reorder lanes, or store a cache.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
