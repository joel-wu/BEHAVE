# Table-Driven FSE Decode Step

This block decodes one Finite State Entropy symbol by indexing a programmable decoding table with the current decoder state. The selected entry supplies the symbol, bit count, and next-state base. Table sizes range from 32 to 4096 entries.

## Interface

### RTL interface

```systemverilog
module compression_fse_ans_fse_decode_step (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_op,
    input  logic [3:0] in_table_log,
    input  logic [15:0] in_state,
    input  logic [31:0] in_bits,
    input  logic [11:0] in_table_index,
    input  logic [7:0] in_symbol,
    input  logic [4:0] in_nb_bits,
    input  logic [15:0] in_new_state,
    output logic out_valid,
    input  logic out_ready,
    output logic [7:0] out_symbol,
    output logic [15:0] out_next_state,
    output logic [4:0] out_bits_consumed,
    output logic out_error
);
```

All payloads are unsigned. Commands transfer on rising edges with `in_valid && in_ready`, outside reset, and take effect in accepted order. Responses transfer with `out_valid && out_ready`; a stalled response retains valid and payload. Input backpressure is permitted; no fixed response latency is required.

### Behavior interface

```python
class CompressionFseAnsFseDecodeStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_op, in_table_log, in_state, in_bits,
                in_table_index, in_symbol, in_nb_bits, in_new_state): ...
```

Each call represents one accepted command. Arguments are unsigned integers
with the corresponding RTL port widths. Writes (`in_op=1`) and clears
(`in_op=2`) return `None`. Decode (`in_op=0`) and reserved (`in_op=3`)
commands return one dictionary, not a list, with exactly these keys:

```python
{"out_symbol": ..., "out_next_state": ..., "out_bits_consumed": ..., "out_error": ...}
```

The first three values are unsigned 8-, 16-, and 5-bit integers,
respectively; `out_error` is integer `0` or `1`. Error responses include all
four keys. A successful zero-bit decode also returns a dictionary, not
`None`. Table contents persist between calls; the caller supplies each
decoder state. `reset()` invalidates the table and returns `None` without
producing a response.

## Modules

- `compression_fse_ans_fse_decode_step`: decoding-table storage, state-indexed lookup, and FSE state update.

## Functional Behavior

Architectural state consists of 4096 entries `(symbol, nb_bits, new_state, valid)`.

| `in_op` | Action | Relevant payloads | Responses |
| --- | --- | --- | --- |
| 0 | Decode | `in_table_log, in_state, in_bits` | One |
| 1 | Write entry | `in_table_index, in_symbol, in_nb_bits, in_new_state` | None |
| 2 | Clear table | None | None |
| 3 | Reserved: return an error | None | One |

A write replaces one entry and marks it valid. A clear invalidates all entries; invalid entries' stored payloads are unobservable. Unlisted payloads are ignored.

For a decode, let `log = in_table_log` and `L = 2^log`:

1. Require `5 <= log <= 12`, `in_state < L`, and a valid entry at `in_state`.
2. Read its `symbol`, `n = nb_bits`, and `base = new_state`. Require `n <= log`.
3. Compute `next = base + (in_bits & (2^n-1))` without truncation. Require `next < L`.
4. Return `out_symbol=symbol`, `out_next_state=next`, `out_bits_consumed=n`, and `out_error=0`.

A failed check or reserved command returns exactly zero for the three data outputs and `out_error=1`. A zero-bit entry consumes nothing and returns its base state. High bits of `in_bits` above the consumed field have no effect. Decoding does not alter the table.

For example, at `log=5`, an entry at state 9 containing `(symbol=7, nb_bits=3, new_state=16)` maps `in_bits=0xAD` to symbol 7, next state 21, and a consumed count of 3. A different `in_state` selects a different entry, not a supplied answer.

The caller supplies the next low-order bitfield from the reverse FSE bitstream and chains the returned state. Table construction, bit-reservoir refill, state initialization/flushing, end-of-stream validation, and multiple-state interleaving are outside this single-step task. Local checks do not certify global table consistency. Raw and RLE modes are not part of the normal-FSE table domain.

## Reset Behavior

Reset is synchronous active-high and takes priority over transfers. It invalidates the table and discards any pending response. After a reset edge, `in_ready=1`, `out_valid=0`, and all response payloads are zero. Reload the table before a successful decode; there is no retained decoder-state register.
