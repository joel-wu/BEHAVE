# cipher

An eight-round native start/done Feistel machine.

## Interface

### RTL interface

```systemverilog
module cipher (
    input logic clk, rst_n, start,
    input logic [31:0] data_in,
    input logic [15:0] key,
    output logic [31:0] data_out,
    output logic done
);
```

### Behavior interface

```python
class Cipher:
    def __init__(self): ...
    def reset(self): ...
    def process(self, start, data_in, key): ...
```

Every `process` call supplies all three arguments, including idle cycles:
`start` is 0 or 1, `data_in` is an unsigned 32-bit integer, and `key` is an
unsigned 16-bit integer. It returns exactly `{"data_out": value, "done": flag}`,
with an unsigned 32-bit value and a 0/1 flag sampled immediately BEFORE
one rising edge of `clk`, then advances that edge. Idle and busy cycles
also return this dictionary, never `None` or a list. State and output pins
persist across calls. Construction and `reset()` restore the initial state;
`reset()` returns `None` and does not represent a clock edge. Physical reset
is applied separately from ordinary input records.

## Modules

- `cipher`: the complete native module above.

## Functional Behavior

This task retains the source's toy eight-round 32-bit Feistel cipher;
it is not DES or a standard cryptographic primitive. The source leaves rotation
amounts and the exact key schedule unspecified. This explicit completion keeps
the previously documented arithmetic: all round values are 16 bits;
initial left=data_in[31:16], right=data_in[15:0]. rotl16(x,k) is the
16-bit circular left rotation ((x<<k)|(x>>(16-k))) mod 65536;
subkey_i=rotl16(latched_key,1) XOR i for i=0..7;
f(r,k)=rotl16((rotl16(r XOR k,3)+k) mod 65536,1).
Each round replaces (left,right) by (right,left XOR f(right,subkey_i)).
The ciphertext is {right_8,left_8}. The completed equation maps
data_in=0x12345678,key=0xABCD to 0xBA61024D. The original testbench's
0x352454F2 is inconsistent with this completion and is NOT claimed to pass;
the original incomplete prose cannot uniquely derive that testbench value.

At idle edge e with start=1, latch both input halves and key. Edges e+1 through
e+8 perform exactly one round each. Edge e+9 publishes ciphertext and raises
done for one cycle, returning to IDLE. Ignore start while in ROUND or FINISH;
the earliest next acceptance is e+10. A held start can launch another block
then. Input changes after acceptance do not affect the active block.
Keep data_out unchanged until the next finish (zero after reset). done clears
each non-finish edge. No artificial ready/valid queue or output stall exists.

## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
