# Zstandard Sequence-Header Prefix Parser

Decode the sequence count and symbol-encoding modes at the start of a Zstandard Sequences section. Stop before FSE table construction and payload decoding. This parser does not establish that the remainder of a compressed block is valid.

## Interface

### RTL interface

```systemverilog
module compression_zstd_dict_zstd_sequence_parser (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [7:0] in_byte0, in_byte1, in_byte2, in_byte3,
    input logic [7:0] in_byte4, in_byte5, in_byte6, in_byte7,
    input logic [5:0] in_size,
    output logic out_valid, input logic out_ready,
    output logic [31:0] out_nbSeq,
    output logic [1:0] out_LLtype, out_OFtype, out_MLtype,
    output logic [3:0] out_header_size,
    output logic out_has_error,
    output logic [2:0] out_error_code
);
```

### Behavior interface

```python
class CompressionZstdDictZstdSequenceParser:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_byte0, in_byte1, in_byte2, in_byte3, in_byte4, in_byte5, in_byte6, in_byte7, in_size): ...
```

Each call represents one accepted prefix and returns exactly one dictionary
with `out_nbSeq` (32 bits), `out_LLtype`, `out_OFtype`, `out_MLtype` (2 bits each),
`out_header_size` (4 bits), `out_has_error` (1 bit), and `out_error_code` (3 bits).
Arguments and fields are unsigned integers of their port widths. Errors return
the same complete record, never `None` or a list. The model is stateless;
`reset()` returns `None` without emitting a record.

### Transaction protocol

Inputs and outputs transfer on rising edges with valid and ready both high,
in order. Stalled outputs retain valid and all fields.

## Modules

- `compression_zstd_dict_zstd_sequence_parser`: prefix decoding and result holding.

## Functional Behavior

`in_size` is the total available Sequences-section length, limited to 0..63 for this task. The byte ports supply its first eight bytes (or the available prefix if shorter). The parser reads at most bytes 0..3; bytes 4..7 and payload beyond the prefix do not affect the result.

Let `b[i]=in_bytei`. An unavailable required byte produces error 1. First read `b[0]`:

| First byte | Sequence count | Count-field length |
| --- | --- | --- |
| 0..127 | `b[0]` | 1 |
| 128..254 | `256*(b[0]-128)+b[1]` | 2 |
| 255 | `32512+b[1]+256*b[2]` | 3 |

If the count is zero, the count-field length must equal `in_size`; otherwise report error 2. A valid zero-count section has zero modes and ends after its count field.

For a nonzero count, read one modes byte immediately after the count field. Its low two bits must be zero, otherwise report error 2. Bits 7..6 give `LLtype`, 5..4 `OFtype`, and 3..2 `MLtype`. Mode codes 0,1,2,3 mean predefined, RLE, FSE-compressed, and repeat respectively. Report the decoded count, modes, and count-field length plus one; repeat-table availability is not checked here.

On success, `out_has_error=0` and `out_error_code=0`. On any error, `out_has_error=1`, `out_error_code` is 1 (truncation) or 2 (corruption), and all five decoded fields are zero. The maximum count is 98047; header size is at most four. A success describes only this prefix, not the subsequent FSE tables or sequence payload.

## Reset Behavior

Reset is synchronous active-high. A reset edge discards the pending result and clears output valid and all output fields. There is no retained parser state; input is ready after reset.
