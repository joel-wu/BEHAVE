# SignalProcessingCommSequenceBSequence

This block implements a stateful fixed-length binary sequence register. The sequence is a `NUM_BITS`-wide vector of one-bit elements. Supported operations are sequence clear, parallel load, serial push, one-bit circular left shift, bitwise XOR and AND against an external operand, population count, bit index readout, and equality correlation against an external operand. The architectural algorithm is a logical `NUM_BITS`-bit shift register: serial push shifts left by one bit and inserts the new bit at the least-significant position, while circular shift reinserts the previous most-significant bit.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_sequence_bsequence #(
    parameter int unsigned NUM_BITS = 32
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic [3:0]                       in_op,
    input  logic                             in_bit,
    input  logic [((NUM_BITS > 1) ? $clog2(NUM_BITS) : 1)-1:0]      in_index,
    input  logic [NUM_BITS-1:0]              in_sequence,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic [NUM_BITS-1:0]              out_sequence,
    output logic                             out_bit,
    output logic [$clog2(NUM_BITS+1)-1:0]    out_count,
    output logic [$clog2(NUM_BITS+1)-1:0]    out_corr
);
```

`NUM_BITS` is any positive integer (default 32), including one and non-powers of two. The index port has `max(1, ceil(log2(NUM_BITS)))` bits.

`in_op` encoding:

- `4'd0` : `RESET_SEQ`
- `4'd1` : `INIT`
- `4'd2` : `PUSH`
- `4'd3` : `CIRCSHIFT`
- `4'd4` : `XOR`
- `4'd5` : `AND`
- `4'd6` : `ACCUMULATE`
- `4'd7` : `INDEX`
- `4'd8` : `CORRELATE`

Outside reset, rising edges with `in_valid && in_ready` accept commands and edges with `out_valid && out_ready` transfer results. Every accepted command produces exactly one ordered output. Stalled valid payloads must remain stable; state changes only for accepted commands.

### Behavior interface

```python
class SignalProcessingCommSequenceBSequence:
    def __init__(self, num_bits=32):
        pass

    def reset(self):
        pass

    def process(self, in_op, in_bit, in_index, in_sequence):
        pass
```

The returned dict contains keys:

- `out_sequence`
- `out_bit`
- `out_count`
- `out_corr`

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_bit`: 1.
- `out_corr`, `out_count`: $clog2(NUM_BITS+1).
- `out_sequence`: NUM_BITS.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

Constructor num_bits corresponds to RTL NUM_BITS. Count and correlation widths use the
same ceiling-log2 expression as the RTL ports.

## Modules

- `signal_processing_comm_sequence_bsequence`: top-level stateful binary-sequence register and operation unit.

## Functional Behavior

Let `S` denote the architectural sequence state, a `NUM_BITS`-bit unsigned vector. Bit numbering is:

- bit 0 is the least-significant and right-most sequence bit,
- bit `NUM_BITS-1` is the most-significant and left-most sequence bit.

All sequence assignments are truncated modulo `2^NUM_BITS`.

For each accepted input transaction, the block reads the pre-transaction state `S_old`, computes outputs, computes the post-transaction state `S_new`, updates the state to `S_new`, and emits one output transaction.

Define:

- `X = in_sequence[NUM_BITS-1:0]`
- `b = in_bit & 1`
- `popcount(V)` = number of one bits in the `NUM_BITS`-bit vector `V`
- `corr(A,B)` = number of indices `i` in `[0, NUM_BITS-1]` for which `A[i] == B[i]`

Operations:

1. `RESET_SEQ` (`in_op = 0`)
   - `S_new = 0`
   - Output:
     - `out_sequence = 0`
     - `out_bit = 0`
     - `out_count = 0`
     - `out_corr = 0`

2. `INIT` (`in_op = 1`)
   - `S_new = X`
   - Output:
     - `out_sequence = X`
     - `out_bit = 0`
     - `out_count = popcount(X)`
     - `out_corr = corr(X, X) = NUM_BITS`

3. `PUSH` (`in_op = 2`)
   - `S_new = ((S_old << 1) | b) mod 2^NUM_BITS`
   - The previous bit `S_old[NUM_BITS-1]` is discarded.
   - Output:
     - `out_sequence = S_new`
     - `out_bit = b`
     - `out_count = popcount(S_new)`
     - `out_corr = 0`

4. `CIRCSHIFT` (`in_op = 3`)
   - `msb = (S_old >> (NUM_BITS-1)) & 1`
   - `S_new = ((S_old << 1) | msb) mod 2^NUM_BITS`
   - Output:
     - `out_sequence = S_new`
     - `out_bit = msb`
     - `out_count = popcount(S_new)`
     - `out_corr = 0`

5. `XOR` (`in_op = 4`)
   - `R = S_old ^ X`
   - `S_new = S_old`
   - Output:
     - `out_sequence = R`
     - `out_bit = 0`
     - `out_count = popcount(R)`
     - `out_corr = 0`

6. `AND` (`in_op = 5`)
   - `R = S_old & X`
   - `S_new = S_old`
   - Output:
     - `out_sequence = R`
     - `out_bit = 0`
     - `out_count = popcount(R)`
     - `out_corr = 0`

7. `ACCUMULATE` (`in_op = 6`)
   - `S_new = S_old`
   - Output:
     - `out_sequence = S_old`
     - `out_bit = 0`
     - `out_count = popcount(S_old)`
     - `out_corr = 0`

8. `INDEX` (`in_op = 7`)
   - If `in_index < NUM_BITS`, `bit_result = (S_old >> in_index) & 1`.
   - If `in_index >= NUM_BITS`, `bit_result = 0`.
   - `S_new = S_old`
   - Output:
     - `out_sequence = S_old`
     - `out_bit = bit_result`
     - `out_count = popcount(S_old)`
     - `out_corr = 0`

9. `CORRELATE` (`in_op = 8`)
   - `S_new = S_old`
   - Output:
     - `out_sequence = S_old`
     - `out_bit = 0`
     - `out_count = popcount(S_old)`
     - `out_corr = corr(S_old, X)`

For any `in_op` value not listed above, behavior is identical to `ACCUMULATE`.

`out_count` and `out_corr` are unsigned integers in the inclusive range `[0, NUM_BITS]`, encoded in `$clog2(NUM_BITS+1)` bits.

## Reset Behavior

The block uses asynchronous active-high reset `rst`. Reset cancels any pending result and initializes the architectural sequence state to zero.

Architectural state after reset:
- `S = 0`

Output values after reset and before the first accepted input:
- `out_valid = 0`
- `out_sequence = 0`
- `out_bit = 0`
- `out_count = 0`
- `out_corr = 0`

No other architectural state exists.
