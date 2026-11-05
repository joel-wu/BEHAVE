# Three-output Frequency Divider

Divide the input clock frequency by 2, 10 and 100 in parallel. For a 100 MHz input these outputs are 50 MHz, 10 MHz and 1 MHz. There is no data input or handshake.

## Interface

### RTL interface

```systemverilog
module freq_div (
    input logic CLK_in,
    input logic RST,
    output logic CLK_50,
    output logic CLK_10,
    output logic CLK_1
);
```

### Behavior interface

```python
class FrequencyDivider:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Call `process()` with no arguments once per non-reset rising edge. It returns
exactly one dictionary with `CLK_50`, `CLK_10`, and `CLK_1`, each an integer
0 or 1 sampled before the edge, then advances state. The return is never
`None` or a list. Reuse the instance across edges. `reset()` clears state,
returns `None`, and emits no record.

### Clock-edge observations

Observe the three settled output levels immediately before each non-reset
rising edge, then advance that edge. Reset edges produce no observation records.

## Modules

- `freq_div`: the native clocked top module shown above.

## Functional Behavior

`CLK_50` toggles on every rising edge of `CLK_in`. `CLK_10` toggles after each group of five rising edges; `CLK_1` toggles after each group of fifty. The counters start at zero, reset to zero when they toggle their output, and otherwise increment by one. All three outputs start low and hold their values between rising edges.

After `n` non-reset rising edges, their levels are `(n mod 2, floor(n/5) mod 2, floor(n/50) mod 2)`. Thus all have 50% duty cycle after reset is released. Output clocks are observed pins; no downstream clocked logic is included.

## Reset Behavior

`RST` is asynchronous and active high. It immediately clears both counters and all three outputs.
