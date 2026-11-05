# CTC Path Collapse and Timestep Extraction

This is `CTCDecoder._get_tokens` / `_get_timesteps` after path decoding, not emission scoring, beam expansion or a language model. Legal parameters satisfy `TOKEN_W >= 1`, `TIME_W >= 1` and `1 <= MAX_T < 2**TIME_W`, so counts and timesteps fit. Ready/valid is the hardware transport adaptation.

This block post-processes one raw CTC decoder path into a compact token sequence and matching token start timesteps. It implements the CTC collapse rule used on a single decoded token-ID stream: consecutive duplicate token IDs are merged by keeping the first occurrence of each run, then all occurrences of the configured blank token are removed. The surviving token IDs and their original frame indices are returned as fixed-width packed vectors with explicit valid lengths.

## Interface

### RTL interface

```systemverilog
module ai_ml_audio_ctc_ctc_beam_search #(
    parameter int TOKEN_W = 8,
    parameter int TIME_W  = 8,
    parameter int MAX_T   = 16
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [TOKEN_W-1:0]           in_blank_token,
    input  logic [TIME_W-1:0]            in_length,
    input  logic [MAX_T*TOKEN_W-1:0]     in_tokens,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [TIME_W-1:0]            out_token_length,
    output logic [TIME_W-1:0]            out_timestep_length,
    output logic [MAX_T*TOKEN_W-1:0]     out_tokens,
    output logic [MAX_T*TIME_W-1:0]      out_timesteps
);
```

Packed-vector element layout is:
- `in_tokens[i*TOKEN_W +: TOKEN_W]` is input token `x[i]`
- `out_tokens[i*TOKEN_W +: TOKEN_W]` is output token `y[i]`
- `out_timesteps[i*TIME_W +: TIME_W]` is output timestep `t[i]`

Only indices `0 .. in_length-1` of the input token array are part of the transaction. `in_length` is interpreted as an unsigned `TIME_W`-bit integer. If `in_length > MAX_T`, the block processes exactly the first `MAX_T` input tokens and ignores the rest.

### Behavior interface

```python
class CTCBeamSearch:
    def __init__(self, token_w=8, time_w=8, max_t=16):
        pass

    def reset(self):
        pass

    def process(self, in_blank_token, in_length, in_tokens):
        pass
```

Constructor arguments `token_w`, `time_w`, and `max_t` correspond to RTL
parameters `TOKEN_W`, `TIME_W`, and `MAX_T`. One `process(...)` call represents
one accepted input transaction, not one clock cycle, and returns one dictionary
with exactly these keys:
- `out_token_length`
- `out_timestep_length`
- `out_tokens`
- `out_timesteps`

Arguments and results are unsigned port words. `in_tokens`, `out_tokens`, and
`out_timesteps` are packed integers, not Python sequences, with the layout above.
Both length values are `TIME_W`-bit integers. An empty collapsed path still
returns all four fields, each zero; it does not return `None` or an empty list.
`reset()` returns `None` and restores the stateless computational starting point.

## Modules

- `ai_ml_audio_ctc_ctc_beam_search`: top-level CTC token collapse and timestep extraction block.

## Functional Behavior

For each accepted input transaction, define the effective processed length

- `L = min(unsigned(in_length), MAX_T)`

and define the input token sequence `x[0 .. MAX_T-1]` as the `TOKEN_W`-bit unsigned tokens unpacked from `in_tokens`.

The block scans token positions `i = 0` to `i = L-1` in ascending order.

A position `i` is selected if and only if both of the following conditions hold:
1. `x[i] != unsigned(in_blank_token)`
2. `i == 0` or `x[i] != x[i-1]`

For every selected position `i`, append:
- token `x[i]` to output token sequence `y`
- timestep `i` to output timestep sequence `t`

The sequences `y` and `t` preserve input order.

This defines the CTC collapse rule exactly:
- each maximal run of equal token IDs contributes only its first element
- blank-token runs contribute nothing because the selected token is later filtered by condition 1
- two equal non-blank token IDs separated by at least one different token ID, including the blank token, produce two output elements because they are not consecutive in the raw input sequence

The output lengths are:
- `out_token_length = len(y)`
- `out_timestep_length = len(t)`
- `len(y) = len(t)`

The packed outputs are formed by writing valid elements first and zero-filling the unused tail:
- for `0 <= k < len(y)`, packed output token field `k` is `y[k]`
- for `len(y) <= k < MAX_T`, packed output token field `k` is `0`
- for `0 <= k < len(t)`, packed output timestep field `k` is `t[k]`
- for `len(t) <= k < MAX_T`, packed output timestep field `k` is `0`

All token fields are reduced modulo `2**TOKEN_W`. All timestep and length fields are reduced modulo `2**TIME_W`.

If `L = 0`, both output lengths are zero and every packed output field is zero.

The block is 1:1: each accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is asynchronous and active-high. Assertion immediately establishes the
following state, without waiting for a rising clock edge:
- no transaction is pending internally
- `out_valid = 0`
- `out_token_length = 0`
- `out_timestep_length = 0`
- `out_tokens = '0`
- `out_timesteps = '0`

There is no other architectural state.
