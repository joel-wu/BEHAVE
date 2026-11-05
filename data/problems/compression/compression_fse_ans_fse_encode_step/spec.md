# Table-Driven FSE Encode Step

This block encodes one Finite State Entropy symbol using programmable symbol-transform and next-state tables. It selects the transform by symbol, emits the low bits of the current encoder state, and looks up the next state. Tables support an 8-bit alphabet and table sizes from 32 to 4096 entries.

## Interface

### RTL interface

```systemverilog
module compression_fse_ans_fse_encode_step (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_op,
    input  logic [3:0] in_table_log,
    input  logic [15:0] in_state,
    input  logic [7:0] in_symbol,
    input  logic [31:0] in_delta_nb_bits,
    input  logic signed [31:0] in_delta_find_state,
    input  logic [11:0] in_table_index,
    input  logic [15:0] in_next_state,
    output logic out_valid,
    input  logic out_ready,
    output logic [4:0] out_nb_bits,
    output logic [15:0] out_bits,
    output logic [15:0] out_next_state,
    output logic out_error
);
```

All payloads are unsigned except the signed two's-complement `in_delta_find_state`. A command transfers on a rising edge with `in_valid && in_ready`, outside reset. Responses transfer with `out_valid && out_ready`; a stalled response retains valid and payload. Commands take effect in accepted order. Input backpressure is permitted, with no fixed response latency.

### Behavior interface

```python
class CompressionFseAnsFseEncodeStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_op, in_table_log, in_state, in_symbol,
                in_delta_nb_bits, in_delta_find_state, in_table_index, in_next_state): ...
```

Each call represents one accepted command. Arguments are unsigned port bit
patterns; in particular, `in_delta_find_state` is an unsigned 32-bit integer
encoding its signed two's-complement value. Configuration commands
(`in_op=1,2,3`) return `None`. An encode command (`in_op=0`) returns one
dictionary with exactly these keys:

```python
{"out_nb_bits": ..., "out_bits": ..., "out_next_state": ..., "out_error": ...}
```

`out_nb_bits` is an unsigned 5-bit integer, `out_bits` and `out_next_state`
are unsigned 16-bit integers, and `out_error` is integer `0` or `1`. Error
responses include all four keys. A successful zero-bit encode still returns
a dictionary, not `None` or a list. Both tables persist between calls; encoder
state is supplied by the caller. `reset()` invalidates the tables and returns
`None` without producing a response.

## Modules

- `compression_fse_ans_fse_encode_step`: symbol-transform storage, next-state storage, and FSE symbol encoding.

## Functional Behavior

Architectural state comprises 256 symbol entries `(delta_nb_bits, delta_find_state, valid)` and 4096 next-state entries `(state, valid)`.

| `in_op` | Action | Relevant payloads | Responses |
| --- | --- | --- | --- |
| 0 | Encode | `in_table_log, in_state, in_symbol` | One |
| 1 | Write symbol entry | `in_symbol, in_delta_nb_bits, in_delta_find_state` | None |
| 2 | Write next-state entry | `in_table_index, in_next_state` | None |
| 3 | Clear both tables | None | None |

A write replaces one entry and marks it valid. Other payloads are ignored. A clear invalidates every entry; its stored payload is then unobservable. Writes need not form a globally valid FSE table; encode requests apply the local checks below.

For an encode, let `log = in_table_log` and `L = 2^log`.

1. Require `5 <= log <= 12`, `L <= in_state < 2L`, and a valid symbol entry at `in_symbol`.
2. Read that entry's `delta_nb_bits` and signed `delta_find_state`. Compute `n = (in_state + delta_nb_bits) >> 16`, with an exact 33-bit unsigned sum, and require `n <= log`.
3. Compute the signed integer address `i = (in_state >> n) + delta_find_state`. Require `0 <= i < L` and a valid next-state entry at `i`.
4. Require that entry's value `next` to satisfy `L <= next < 2L`.
5. Return `out_nb_bits=n`, `out_bits=in_state & (2^n-1)`, `out_next_state=next`, and `out_error=0`.

A failed check returns exactly zero for the three data outputs and `out_error=1`. Arithmetic is not silently wrapped into a valid address or bit count. Encoding does not change either table. The caller chains `out_next_state` into later requests and places the returned low-bit field into its bitstream.

For example, at `log=5`, write symbol 7 with `delta_nb_bits=65504, delta_find_state=-16`, and state-table entry 5 with value 47. Encoding state 43 and symbol 7 emits `n=1, bits=1, next=47`. Changing entry 5 or the symbol's transform changes the actual lookup result.

Table construction, frequency normalization, stream packing, end-of-stream markers, and interleaving multiple FSE states are outside this single-step task. Raw and RLE block modes are also outside its normal-FSE table domain.

## Reset Behavior

Reset is synchronous active-high and takes priority over transfers. It invalidates both tables and discards any pending response. After a reset edge, `in_ready=1`, `out_valid=0`, and all response payloads are zero. Tables must be reloaded before a successful encode; there is no retained encoder-state register.
