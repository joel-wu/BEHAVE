# cvdp_agentic_spi_complex_mult_0002

Implement the source serial complex multiplier.

## Interface

### RTL interface

```systemverilog
module spi_complex_mult #(
    parameter int IN_WIDTH=16, parameter int OUT_WIDTH=32
)(input logic rst_async_n, spi_sck_level, spi_cs_n, spi_mosi,
  output wire spi_miso);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, IN_WIDTH=16, OUT_WIDTH=32): ...
    def reset(self): ...
    def process(self, rst_async_n, spi_sck_level, spi_cs_n, spi_mosi): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `spi_miso`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `spi_complex_mult`: the stateful digital circuit below.

## Functional Behavior

Receive signed Ar, Ai, Br, Bi in that order, each IN_WIDTH bits,
MSB first, and compute real=Ar*Br-Ai*Bi and imag=Ar*Bi+Ai*Br. Transmit the
preceding group's real then imaginary result, each OUT_WIDTH bits MSB first,
while receiving the next group. The first group after reset transmits zeros.
Keep each result modulo 2^OUT_WIDTH: the imaginary sum can overflow even
when OUT_WIDTH=2*IN_WIDTH (four minimum signed operands produce +2^(2N-1)).
There is no saturation. Supported IN_WIDTH is a positive multiple of eight,
8..32; OUT_WIDTH must equal 2*IN_WIDTH so full-duplex groups have equal length.
Defaults 16/32 match the source. This parameter relationship completes a
missing source restriction rather than claiming all width pairs are supported.

SPI Mode 0 samples MOSI on each rising spi_sck_level edge while selected
(spi_cs_n=0). MISO presents the next bit during the low phase and holds that
bit throughout the high phase; a conventional falling edge launches it.
Thus the first bit is available when selected with SCK low, before the first
rising edge. Repeated pin records do not clock any serial state.
Deselected MISO is defined as zero, not high impedance.

The source TB deasserts CS after EVERY BYTE, not after each operand group.
Completed bytes therefore survive CS deassertion. Continuous selection is
also supported. Deasserting CS discards only the incomplete input byte and
restarts transmission of that output byte; previous completed bytes survive.
The eighth accepted bit commits a byte, even if no falling edge follows it.
After the final input byte, the new result is available for the next group;
the just-sampled final output bit remains held until the low phase.

rst_async_n=0 asynchronously clears byte/group assembly, stored results and
the held output bit. It takes priority over CS and clock events. On a record
with CS high no bit is accepted, including a coincident rising clock. For raw
non-Mode-0 records, asserting CS with SCK high exposes the held bit until the
next low phase; no synthetic edge is inserted. A rising SCK coincident with
CS assertion samples the new MOSI. These rules make digital observations
total without claiming analog setup/hold safety.

The original spi_sck is renamed spi_sck_level and supplied as physical levels.
The RTL retains a real rising-edge receiver and a low-phase output latch.
Fresh storage and the prior clock level are zero. The source supplied no
answer RTL; byte persistence follows its TB, while partial-byte abort, inactive
MISO, startup and overflow are explicitly completed here. No frame handshake
or ready/valid wrapper replaces the serial protocol.

## Reset Behavior

The physical reset pin has the polarity and state effects specified above. Fresh execution initializes all internal storage and prior input levels to zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
