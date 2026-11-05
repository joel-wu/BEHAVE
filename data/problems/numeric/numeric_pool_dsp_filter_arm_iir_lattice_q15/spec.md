# Four-Stage Q15 IIR Lattice Filter

Filters a signed Q1.15 sample stream through four reflection stages and a five-term ladder. Stage saturation, delayed state and coefficient order are part of the function.

## Interface

### RTL interface

```systemverilog
module q15_iir_lattice_filter_4stage (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);
```

Samples are two's-complement 16-bit values. Rising edges with valid and ready both high transfer a sample. Input backpressure is permitted. While `out_valid=1` and `out_ready=0`, output data and valid remain stable.

### Behavior interface

```python
class Q15IirLatticeFilter4Stage:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_sample): ...
```

`process()` returns `{"out_sample": bits}` for each input.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_sample`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q15_iir_lattice_filter_4stage`: the complete lattice/ladder filter and ordered sample interface.

## Functional Behavior

Use the signed integer coefficient arrays `K=[8192, -8192, 4096, -4096]` and `V=[4096, 8192, -8192, 2048, -2048]`. Their entries represent Q1.15 values. Four delays `d[0..3]` start at zero. For each accepted signed sample `x`, evaluate the following using the **old** delays:

```text
f = x
for i = 0..3:
    f = sat16(f - floor(d[i] * K[i] / 32768))
    g[i] = sat16(d[i] + floor(f * K[i] / 32768))
y = sat16(floor((sum(g[i] * V[i] for i=0..3) + f * V[4]) / 32768))
next_d = [g[1], g[2], g[3], f]
```

`sat16` clamps to `[-32768,32767]`; `floor(.../32768)` is a signed arithmetic right shift by 15, without rounding. Products and the ladder sum are evaluated without overflow. Saturate both intermediate stage values, not just the final output. The shifted delay assignment above is simultaneous and is essential to the recurrence. Encode `y` in the low 16 bits of `out_sample`.

Every input produces exactly one output in accepted-input order. Bubbles and output stalls do not advance filter state; latency is not fixed. When `out_valid=0`, drive `out_sample=0`.

## Reset Behavior

`rst` is active high and synchronous. A reset edge clears all four delays and discards any pending output. After that edge `in_ready=1`, `out_valid=0`, and `out_sample=0`. Coefficients are unchanged by reset.
