# Brotli Context Lookup

This block maps the previous two bytes and a Brotli context mode to the corresponding six-bit context identifier. It implements all four Brotli context models, including UTF-8 byte classes and signed-integer byte classes.

## Interface

### RTL interface

```systemverilog
module compression_brotli_brotli_context_lookup (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [1:0] in_mode,
    input  logic [7:0] in_p1,
    input  logic [7:0] in_p2,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_context
);
```

Inputs are unsigned. `in_p1` is the most recent byte and `in_p2` the preceding byte. The 8-bit output carries a value in 0–63; its top two bits are zero.

### Behavior interface

```python
class BrotliContextLookup:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_mode, in_p1, in_p2): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_context": context}`, where `context` is a Python integer holding the
unsigned output port bit pattern. A zero context is still one output record.

## Modules

- `compression_brotli_brotli_context_lookup`: stateless context classification and one ordered response per request.

## Functional Behavior

Inputs transfer on rising edges with `in_valid && in_ready`, outside reset. Outputs transfer with `out_valid && out_ready`; valid and payload remain unchanged while stalled. Input backpressure is allowed and no fixed latency is required.

The mode selects:

| Mode | Result |
| --- | --- |
| 0: LSB6 | `in_p1 & 63` |
| 1: MSB6 | `in_p1 >> 2` |
| 2: UTF8 | `last(in_p1) OR previous(in_p2)` |
| 3: SIGNED | `(class(in_p1) << 3) OR class(in_p2)` |

For UTF8, the ASCII tables below give decimal values. Row and column labels are hexadecimal byte offsets; for example row 20, column 01 describes byte 0x21.

`last` for bytes 0x00–0x7F:

```text
offset   0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
  00     0  0  0  0  0  0  0  0  0  4  4  0  0  4  0  0
  10     0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0
  20     8 12 16 12 12 20 12 16 24 28 12 12 32 12 36 12
  30    44 44 44 44 44 44 44 44 44 44 32 32 24 40 28 12
  40    12 48 52 52 52 48 52 52 52 48 52 52 52 52 52 48
  50    52 52 52 52 52 48 52 52 52 52 52 24 12 28 12 12
  60    12 56 60 60 60 56 60 60 60 56 60 60 60 60 60 56
  70    60 60 60 60 60 56 60 60 60 60 60 24 12 28 12  0
```

`previous` for bytes 0x00–0x7F:

```text
offset   0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
  00     0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0
  10     0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0
  20     0  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1
  30     2  2  2  2  2  2  2  2  2  2  1  1  1  1  1  1
  40     1  2  2  2  2  2  2  2  2  2  2  2  2  2  2  2
  50     2  2  2  2  2  2  2  2  2  2  2  1  1  1  1  1
  60     1  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3
  70     3  3  3  3  3  3  3  3  3  3  3  1  1  1  1  0
```

For non-ASCII bytes:
- `last(b) = b & 1` for 0x80–0xBF, and `2 | (b & 1)` for 0xC0–0xFF.
- `previous(b) = 0` for 0x80–0xDF, and 2 for 0xE0–0xFF.

For SIGNED, both bytes use the same classification:

| Byte range (hexadecimal) | Class |
| --- | --- |
| 00 | 0 |
| 01–0F | 1 |
| 10–3F | 2 |
| 40–7F | 3 |
| 80–BF | 4 |
| C0–EF | 5 |
| F0–FE | 6 |
| FF | 7 |

For example, UTF8 mode with two spaces (0x20, 0x20) returns 8. With `p1='a'` (0x61) and `p2='A'` (0x41), it returns 58. SIGNED mode with `p1=0xFF, p2=0x10` returns 58.

Every mode and byte combination is defined, including combinations that are not a valid UTF-8 sequence. The block classifies bytes; it does not validate UTF-8, maintain history, select a context map, or encode a compressed stream.

## Reset Behavior

Reset is synchronous active-high and overrides handshakes. Discard any pending response and set `out_valid=0, out_context=0`. The reset state is ready for input. There is no context history retained between transactions.
