# Configurable M-Sequence Generator

Maintain a programmable binary linear-feedback shift register with explicit configuration, single-bit advance, seed restart and state readout. Feedback is a binary dot product over GF(2). A suitable generator polynomial and nonzero seed produce a maximal-length sequence; arbitrary polynomials and the all-zero seed remain valid inputs without a maximal-period guarantee.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_sequence_msequence (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_op,
    input  logic [4:0]  in_m,
    input  logic [30:0] in_g,
    input  logic [30:0] in_a,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [30:0] out_bit,
    output logic [30:0] out_state,
    output logic [30:0] out_n
);
```

### Behavior interface

```python
class SignalProcessingCommSequenceMsequence:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_op, in_m, in_g, in_a): ...
```

All payloads are unsigned. Each accepted command returns one dictionary with `out_bit`, `out_state` and `out_n`, corresponding to one output transaction. `out_bit` uses only bit zero of its 31-bit port.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_bit`, `out_n`, `out_state`: 31.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_comm_sequence_msequence`: programmable seeded LFSR and ordered command/result interface.

## Functional Behavior

The stored configuration is length `m`, generator mask `g`, restart seed `a`, and current state `S`. Define `n = 2^m - 1`.

| in_op | Operation | Effect |
| --- | --- | --- |
| 0 | CONFIGURE | For `2 <= in_m <= 31`, store `m=in_m, g=in_g, a=in_a, S=in_a`. Lengths zero and one leave every stored value unchanged. |
| 1 | ADVANCE | Compute `b=parity(S & g)`, then `S=((S << 1) | b) & n`. Configuration inputs are ignored. |
| 2 | RESTART | Restore `S=a`, keeping `m,g,a`; all configuration inputs are ignored. |
| 3 | READ | Leave all state unchanged; all configuration inputs are ignored. |

Each command returns the resulting `S` as `out_state` and the stored length mask `n` as `out_n`. `out_bit=b` only for ADVANCE and is zero for other commands. `parity` is the reduction XOR over all 31 bits. A seed is loaded verbatim, including bits above `m-1`; these bits participate in the first feedback dot product, and the shift result is then masked to `m` bits. A zero state remains zero on ADVANCE, but CONFIGURE or RESTART can restore a nonzero seed.

For example, CONFIGURE `(m=3,g=6,a=1)` returns `(out_bit=0,out_state=1,out_n=7)`. Seven ADVANCE commands then return states `2,5,3,7,6,4,1`, after which the sequence repeats. RESTART returns to state one without advancing.

Outside reset, rising edges with `in_valid && in_ready` accept commands and edges with `out_valid && out_ready` transfer results. A stalled output must retain its valid bit and payload; an input must remain stable until accepted. Results preserve command order, and state advances only for accepted commands. Simultaneous input and output transfers are permitted.

## Reset Behavior

`rst` is asynchronous active-high. It cancels pending output and sets `m=2, g=3, a=1, S=1`. All output payload ports and `out_valid` clear to zero; the first subsequent command observes the initialized state. The RESTART command is distinct from hardware reset: it restores the configured seed without changing the configuration.
