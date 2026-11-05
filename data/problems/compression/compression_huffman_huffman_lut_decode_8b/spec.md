# Configurable Huffman X1 Decode Lookup

This block performs a single-symbol Huffman fast-table lookup. A programmable 2048-entry table maps a bitstream prefix to an 8-bit symbol and the number of compressed bits consumed. Table writes and decode requests share one ordered command interface.

## Interface

### RTL interface

```systemverilog
module compression_huffman_huffman_lut_decode_8b (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_write,
    input  logic [10:0] in_dt_log,
    input  logic [10:0] in_lookup_bits,
    input  logic [7:0]  in_symbol,
    input  logic [5:0]  in_nb_bits,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_symbol,
    output logic [5:0]  out_nb_bits,
    output logic        out_error
);
```

All payloads are unsigned. `in_write` selects a table write (1) or decode (0).

### Behavior interface

```python
class CompressionHuffmanHuffmanLutDecode8b:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_write, in_dt_log, in_lookup_bits, in_symbol, in_nb_bits): ...
```

Call `process` once per accepted input. A table write returns `None`. A decode
returns one dictionary `{"out_symbol": ..., "out_nb_bits": ..., "out_error": ...}`,
including for an invalid entry. Returned fields are Python integers holding
the specified port bit patterns; `out_error` is `0` or `1`.

## Modules

- `compression_huffman_huffman_lut_decode_8b`: table storage, ordered configuration writes, and X1 decode lookup.

## Functional Behavior

A command is accepted on a rising edge with `in_valid && in_ready`, outside reset. Commands take effect in accepted order. Output responses transfer with `out_valid && out_ready` and remain valid with unchanged payload during a stall. Input backpressure is allowed; no fixed latency is required.

The architectural state consists of 2048 entries `(symbol, nb_bits)`.

For a **write**, use the full 11-bit `in_lookup_bits` as the entry address and replace that entry with `(in_symbol, in_nb_bits)`. Ignore `in_dt_log` and emit no response. A stored length zero invalidates that entry.

For a **decode**, ignore `in_symbol` and `in_nb_bits`:

1. If `in_dt_log` is outside 1–11, return an error.
2. Otherwise select `index = in_lookup_bits & ((1 << in_dt_log) - 1)`.
3. If the selected entry has `nb_bits=0` or `nb_bits>in_dt_log`, return an error.
4. Otherwise return the entry's symbol and bit count with `out_error=0`.

An error response is exactly `out_symbol=0, out_nb_bits=0, out_error=1`. A decode never changes the table.

The lowest `in_dt_log` bits hold the prefix, with the earliest stream bit at position `in_dt_log-1`. A valid fast table repeats a short code's entry for every possible suffix. For example, at `dt_log=3`, a table for codes `000:C`, `001:D`, `01:B`, and `1:A` contains:

| Indices | Symbol | Bits consumed |
| --- | --- | --- |
| 0 | C (0x43) | 3 |
| 1 | D (0x44) | 3 |
| 2–3 | B (0x42) | 2 |
| 4–7 | A (0x41) | 1 |

The caller loads the table and advances its bitstream by the returned count. This block does not construct or globally validate a codebook, refill a bit reservoir, or decompress a complete frame. It supports arbitrary entry writes; the local error checks above do not establish global prefix-code validity.

## Reset Behavior

Reset is synchronous active-high and overrides handshakes. Clear every table entry to `(0,0)`, discard any pending response, and set `out_valid=0, out_symbol=0, out_nb_bits=0, out_error=0`. The block is ready for commands; a decode before a suitable table write returns an error.
