# cvdp_copilot_apb_gpio

Native APB GPIO register bank with continuously synchronized edge/level interrupts.

## Interface

### RTL interface

```systemverilog
module cvdp_copilot_apb_gpio #(parameter int GPIO_WIDTH=8)(
    input logic pclk, preset_n, psel,
    input logic [7:2] paddr,
    input logic penable, pwrite,
    input logic [31:0] pwdata,
    input logic [GPIO_WIDTH-1:0] gpio_in,
    output logic [31:0] prdata,
    output logic pready, pslverr,
    output logic [GPIO_WIDTH-1:0] gpio_out, gpio_enable, gpio_int,
    output logic comb_int
);
```

### Behavior interface

```python
class ApbGpio:
    def __init__(self, GPIO_WIDTH=8): ...
    def reset(self): ...
    def process(self, psel, paddr, penable, pwrite, pwdata, gpio_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `comb_int`, `pready`, `pslverr`: 1.
- `gpio_enable`, `gpio_int`, `gpio_out`: GPIO_WIDTH.
- `prdata`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `pclk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `cvdp_copilot_apb_gpio`: the complete native module above.

## Functional Behavior

GPIO_WIDTH is in 1..32, default 8; the 32-bit APB word covers all pins.
The physical six-bit address port is paddr[7:2]. Therefore the register byte
offsets 0x00,04,08,0C,10,14,18 are pin values 0,1,2,3,4,5,6 respectively.
Offsets select synchronized input (read-only), output value, output enable,
interrupt enable, interrupt type, interrupt polarity, and interrupt status
(read-only). Undefined reads return zero and writes do nothing. Writes take
effect only on rising edges with psel && penable && pwrite; retain the low
GPIO_WIDTH bits. prdata is the combinational pre-edge register value on an
active read, otherwise zero. pready is one outside reset, zero during reset;
pslverr is always zero. This resolves the source's always-ready versus
all-outputs-zero-in-reset wording. Reads/writes have no added wait states.

Independently of APB traffic, every rising edge advances a two-register input
synchronizer: sync1'=gpio_in, sync2'=sync1. Keep history'=sync2 to detect changes
in the synchronized value. A rising edge is sync2 & ~history; a falling edge
is ~sync2 & history. Do not compare sync2 against the newer sync1, which reverses
the edge polarity. GPIO output and direction are continuous register outputs.

For each enabled interrupt bit, type=1 selects edge mode and type=0 level mode;
polarity=1 selects rising/high and polarity=0 falling/low. Edge interrupts last
one cycle; level interrupts remain asserted at the selected level. Status is
not sticky and requires no acknowledgement. gpio_int contains ALL asserted
bits and comb_int is their OR; the source's lower-pin priority phrase adds no
arbitration because no encoded interrupt-ID port exists. Read address 6 returns
the same vector. Two-stage synchronization takes precedence over the source's
incompatible raw-input single-cycle-latency sentence. Asynchronous active-low
reset clears all storage, synchronizer history and outputs.

## Reset Behavior

`preset_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
