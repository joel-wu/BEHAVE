# decoder_8b10b

Source-table 8b10b decoder with native valid-only input and one-cycle registered output.

## Interface

### RTL interface

```systemverilog
module decoder_8b10b (
    input logic clk_in, reset_in, control_in,
    input logic [9:0] decoder_in,
    input logic decoder_valid_in,
    output logic [7:0] decoder_out,
    output logic decoder_valid_out, control_out
);
```

### Behavior interface

```python
class Decoder8b10b:
    def __init__(self): ...
    def reset(self): ...
    def process(self, control_in, decoder_in, decoder_valid_in): ...
```

Call `process` once per rising edge of `clk_in`, supplying all three arguments
even when `decoder_valid_in=0`. It returns one dictionary with exactly
`decoder_out`, `decoder_valid_out`, and `control_out`: the settled output pin
values immediately before that edge. It then advances the state through the
edge. Arguments and returned values are Python integers carrying unsigned port
bit patterns; one-bit values are 0 or 1. An invalid cycle still returns this
dictionary, not `None`. `reset()` restores the initial state and returns `None`.

## Modules

- `decoder_8b10b`: the complete native module above.

## Functional Behavior

Capture on each rising edge of clk_in with decoder_valid_in=1. Immediately
after that edge decoder_out is the decoded byte, control_out is the captured
control_in, and decoder_valid_out=1. Consecutive valid edges produce consecutive
outputs. An invalid edge clears decoder_valid_out but holds decoder_out and
control_out; this explicit invalid-cycle completion does not consume a symbol.
There is exactly one output register, no FIFO, output ready or input ready pin.

The following tables are the original task's normative decoder, not a running-
disparity checker. When control_in=1 use the full ten-bit control table; otherwise
decode the upper six and lower four bits independently and concatenate the
three-bit lower-table value above the five-bit upper-table value. An unlisted
control word decodes to zero; an unlisted data subword decodes to zero in THAT
subfield. This defines invalid words without inventing an absent error port.
control_out reports the selected input path even for an unlisted control word.

No extra alternate-7 or running-disparity acceptance is implied beyond these
source tables. The source's single-cycle interface takes precedence over its
vague discussion of multiple pipeline registers. Asynchronous active-high reset
clears all three output registers.

| control word | decoded value |
| --- | --- |
| 0001010111 | 247 |
| 0010010111 | 251 |
| 0011110010 | 156 |
| 0011110011 | 124 |
| 0011110100 | 28 |
| 0011110101 | 92 |
| 0011110110 | 220 |
| 0011111000 | 252 |
| 0011111001 | 60 |
| 0011111010 | 188 |
| 0100010111 | 253 |
| 0111101000 | 254 |
| 1000010111 | 254 |
| 1011101000 | 253 |
| 1100000101 | 188 |
| 1100000110 | 60 |
| 1100000111 | 252 |
| 1100001001 | 220 |
| 1100001010 | 92 |
| 1100001011 | 28 |
| 1100001100 | 124 |
| 1100001101 | 156 |
| 1101101000 | 251 |
| 1110101000 | 247 |

| six-bit subword | decoded value |
| --- | --- |
| 000101 | 23 |
| 000110 | 8 |
| 000111 | 7 |
| 001001 | 27 |
| 001010 | 4 |
| 001011 | 20 |
| 001100 | 24 |
| 001101 | 12 |
| 001110 | 28 |
| 010001 | 29 |
| 010010 | 2 |
| 010011 | 18 |
| 010100 | 31 |
| 010101 | 10 |
| 010110 | 26 |
| 010111 | 15 |
| 011000 | 0 |
| 011001 | 6 |
| 011010 | 22 |
| 011011 | 16 |
| 011100 | 14 |
| 011101 | 1 |
| 011110 | 30 |
| 100001 | 30 |
| 100010 | 1 |
| 100011 | 17 |
| 100100 | 16 |
| 100101 | 9 |
| 100110 | 25 |
| 100111 | 0 |
| 101000 | 15 |
| 101001 | 5 |
| 101010 | 21 |
| 101011 | 31 |
| 101100 | 13 |
| 101101 | 2 |
| 101110 | 29 |
| 110001 | 3 |
| 110010 | 19 |
| 110011 | 24 |
| 110100 | 11 |
| 110101 | 4 |
| 110110 | 27 |
| 111000 | 7 |
| 111001 | 8 |
| 111010 | 23 |

| four-bit subword | decoded value |
| --- | --- |
| 0001 | 7 |
| 0010 | 4 |
| 0011 | 3 |
| 0100 | 0 |
| 0101 | 2 |
| 0110 | 6 |
| 1001 | 1 |
| 1010 | 5 |
| 1011 | 0 |
| 1100 | 3 |
| 1101 | 4 |
| 1110 | 7 |

## Reset Behavior

`reset_in` is asynchronous, active high. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
