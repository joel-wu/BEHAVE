# hill_cipher

Encrypt a three-letter block with a 3x3 Hill-cipher matrix in three computation cycles.

## Interface

### RTL interface

```systemverilog
module hill_cipher (
    input logic clk, reset, start,
    input logic [14:0] plaintext,
    input logic [44:0] key,
    output logic [14:0] ciphertext,
    output logic done
);
```

### Behavior interface

```python
class HillCipher:
    def __init__(self): ...
    def reset(self): ...
    def process(self, start, plaintext, key): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ciphertext`: 15.
- `done`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `hill_cipher`: the complete native module above.

## Functional Behavior

Interpret plaintext as three unsigned five-bit values P0,P1,P2 from MSB
to LSB, and key as nine unsigned five-bit values K00,K01,...,K22 in MSB-first
row-major order. Compute Ci = (sum over j of Kij*Pj) mod 26. Pack C0,C1,C2
MSB first. Letters A..Z correspond to 0..25. As a total-domain completion,
the same arithmetic applies to bit patterns 26..31; results still lie in 0..25.
Do not truncate the sum or insert a modulo-64 operation.

In IDLE, capture plaintext and key on an edge with start=1. Compute one row
on each of the next three edges. On the third edge publish the complete result,
assert done for one cycle and return to IDLE. Starts and data changes during
those three computation edges are ignored. Retain the last completed output
while idle or computing. A new request can be captured at the following edge,
when the previous done pulse clears. No output handshake is introduced.

For CAT=(2,0,19) and key rows (3,10,20),(1,9,15),(8,7,17), the exact residues
are (22,1,1), spelling WBB. The source's UBB spelling is incorrect.

## Reset Behavior

`reset` is asynchronous, active high. Assertion immediately restores the initial state. Controller=IDLE; captured inputs, partial result, ciphertext and done=0.
After reset release, resume the edge-by-edge behavior above.
