# rc5_enc_16bit

Execute one RC5-8 round in four clocks using the original CA_1 key generator and native start/done pins.

## Interface

### RTL interface

```systemverilog
module rc5_enc_16bit (
    input logic clock, reset, enc_start,
    input logic [15:0] p,
    output logic [15:0] c,
    output logic enc_done
);
```

### Behavior interface

```python
class Rc5Enc16bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, enc_start, p): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `c`: 16.
- `enc_done`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clock`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `rc5_enc_16bit`: the complete native module above.

## Functional Behavior

This is a one-round RC5-8 educational encryption controller, not a secure
general-purpose cipher. Split plaintext into A=p[15:8] and B=p[7:0]. All words
and subkeys are unsigned 8-bit values. Additions wrap modulo 256. ROL8(x,n)
rotates x left by n modulo 8 bits, including ROL8(x,0)=x.

The CA state starts at FF. Advance it once on EVERY non-reset rising edge,
including idle and busy edges, with the supplied CA_1 null-boundary rule:
`next_ca(x) = ((x << 1) XOR (x >> 1) XOR (x AND 2A)) AND FF`.
Hexadecimal constants are used here. This is Rule90 at bits 7,6,4,2,0 and
Rule150 at bits 5,3,1; missing neighbors outside bits 0..7 are zero.

Accept p on a rising edge E iff the controller is idle and enc_start=1.
Let q be the CA value immediately BEFORE E. The four keys for this block are
S0=q, S1=next_ca(q), S2=next_ca(S1), S3=next_ca(S2). Capture the plaintext and
keys at acceptance: later input changes do not change this block. The CA still
advances only once per clock, not four times on E. Execute four clocked phases:

| Edge | Action |
| --- | --- |
| E | A=(p[15:8]+S0) mod 256; B=(p[7:0]+S1) mod 256 |
| E+1 | A=(ROL8(A XOR B,B)+S2) mod 256 |
| E+2 | B=(ROL8(B XOR A,A)+S3) mod 256, using the NEW A |
| E+3 | Set c={A,B}, pulse enc_done=1, and return to idle |

Ignore enc_start and p on E+1 through E+3, including the completion edge.
The earliest next acceptance is E+4. A continuously high enc_start therefore
starts one block every four clocks. enc_done is high for exactly one cycle per
completion; clear it on the next edge. c holds its most recently completed
ciphertext through idle and during later encryption, until another completion.
There is no ready port, acknowledgement, backpressure or accepted-input wrapper.

Source completions: the prompt leaves key collection timing, start/busy behavior
and completion pulse width unspecified. The rules above fix them. The original
CA_1 has active-high synchronous reset, while the requested top-level permits
active-low synchronous reset: invert that reset convention at the CA boundary.
The task's illustrative FFFF->0703 calculation uses keys 20,10,FF,FF; it is an
arithmetic example, NOT the output expected from the FF-seeded CA stream.
Only the CA component is supplied upstream; no top-level implementation or
testbench is available to establish unchanged upstream equivalence.


## Reset Behavior

`reset` is synchronous, active low. Only a rising edge of `clock` with reset asserted restores the initial state. CA=FF, controller idle; A, B, captured subkeys, c and enc_done are zero. Reset aborts any in-flight block.
After reset release, resume the edge-by-edge behavior above.
