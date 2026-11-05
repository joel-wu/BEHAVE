# Mamba2 Chunk-State Passing — Scalar Lane

Propagate one scalar state across an SSM chunk using finite exponential decay or amplification and a supplied chunk update. The transaction exposes entering, next and final state separately, preserving the source chunk-state sequence. State and sequence history are explicit ports rather than hidden registers.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_mamba2_mamba2_state_passing (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic in_first_chunk, in_last_chunk, in_has_initstate, in_has_seq_idx,
    input logic [31:0] in_prev_state, in_state_update, in_dA_chunk_cumsum, in_initial_state,
    input logic [31:0] in_prev_seq_idx, in_seq_idx_new,
    output logic out_valid,
    input logic out_ready,
    output logic out_last_chunk,
    output logic [31:0] out_out, out_next_state, out_final_state, out_next_prev_seq_idx
);
```

The state, update and cumulative-exponent fields are Q16.16 numerical values. Sequence-index fields are unsigned 32-bit integers. `in_first_chunk` selects initialization, `in_last_chunk` marks the final chunk, `in_has_initstate` enables a supplied initial state, and `in_has_seq_idx` enables sequence-boundary suppression.

Input and output transfers occur on rising clock edges when valid and ready are both high and `rst` is low. The producer holds the entire input bundle and valid stable while stalled; the block holds all output fields and `out_valid` stable while stalled. One accepted input produces exactly one ordered output unless reset intervenes. No fixed pipeline latency is required.

### Behavior interface

```python
class Mamba2StatePassing:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_first_chunk, in_last_chunk, in_has_initstate, in_has_seq_idx,
                in_prev_state, in_state_update, in_dA_chunk_cumsum, in_initial_state,
                in_prev_seq_idx, in_seq_idx_new): ...
```

Arguments and results use raw port bit patterns. Return one dictionary containing `out_last_chunk`, `out_out`, `out_next_state`, `out_final_state` and `out_next_prev_seq_idx`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`, `out_next_prev_seq_idx`, `out_next_state`, `out_out`: 32.
- `out_last_chunk`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_ssm_mamba2_mamba2_state_passing`: scalar chunk propagation with explicit recurrence state.

## Functional Behavior

All numerical lanes are signed 32-bit Q16.16: raw integer `v` represents `v/65536`. The buses carry their two's-complement bit patterns; all patterns are legal. Sequence indices, where present, are unsigned integers, not fixed point.

Let `S=65536`, `Q=1099511627776` (2^40), and `L=762123384786`. Define `R(n,d)` for positive `d` as nearest-integer division `n/d`, with exact half-way cases rounded to the even integer. Negative results use the same symmetric rule. Define `sat(v)=min(2147483647,max(-2147483648,v))`, `add(a,b)=sat(a+b)`, and `mul(a,b)=sat(R(a*b,S))`. Intermediates within these definitions are mathematical integers; only the stated boundaries saturate.

The following integer recurrence defines the exponential approximation `E(v)`. It is part of the numerical contract, not a prescribed circuit or latency.

```text
F(v):                         # internal Q40 exponential; callers have v < 11*S
    if v <= -32*S: return 0
    k = floor((v*2^24)/L)
    r = v*2^24 - k*L
    term = total = Q
    for j = 1..12:
        term = R(term*r, Q*j)
        total = total + term
    return total*2^k if k >= 0 else R(total, 2^(-k))

E(v):
    if v >= 11*S: return 2147483647
    if v <= -16*S: return 0
    return sat(R(F(v), 2^24))
```

`L` is the nearest Q40 representation of ln(2); range reduction and all twelve polynomial terms are retained. The underflow and saturation branches do not replace finite nonzero exponentials inside the representable range. This is a fixed-point approximation contract, not IEEE-754 arithmetic; there are no NaN or infinity encodings.

For each input, select the entering state and sequence history:

```text
if in_first_chunk:
    carried = in_initial_state if in_has_initstate else 0
    previous_index = 0
else:
    carried = in_prev_state
    previous_index = in_prev_seq_idx

if in_has_seq_idx and in_seq_idx_new != previous_index:
    contribution = 0
else:
    contribution = mul(E(in_dA_chunk_cumsum), carried)
next_state = add(contribution, in_state_update)
```

A sequence boundary suppresses the old state contribution; it does not discard the new chunk update. Form the outputs:

```text
out_last_chunk = in_last_chunk
out_out = carried
out_next_state = next_state
out_final_state = next_state if in_last_chunk else 0
out_next_prev_seq_idx = in_seq_idx_new if in_has_seq_idx else 0
```

`out_out` is the entering state for this chunk, corresponding to the source's per-chunk state sequence; it is not another copy of the next state. Feed `out_next_state` and `out_next_prev_seq_idx` back as the following chunk's previous fields. The final-state output is zero on non-final transactions. On the first chunk, stale previous-state/index inputs are ignored; sequence comparison begins at index zero even when initial state is enabled.

For zero cumulative exponent and no sequence change, next state is `add(carried, update)`. Finite positive and negative exponents produce finite amplification and decay until the explicitly defined fixed-point saturation or underflow is reached.

## Reset Behavior

Reset is synchronous and active-high. On a rising edge with `rst=1`, discard every pending output, clear `out_valid`, and set every output payload field to zero. No transfer is counted during reset. The numerical state is supplied explicitly, so reset clears transport state only; it does not modify a caller's saved state. After reset the block can accept a new transaction.
