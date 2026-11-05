# cvdp_agentic_sigma_delta_audio_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module sigma_delta_audio (
    input logic clk_sig_level, clk_en_sig,
    input logic [14:0] load_data_sum, read_data_sum,
    output logic left_sig, right_sig
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clk_sig_level, clk_en_sig, load_data_sum, read_data_sum): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `left_sig`, `right_sig`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `sigma_delta_audio`: the stateful digital circuit below.

## Functional Behavior

Implement enabled-clock stereo interpolation and second-order one-bit
error-feedback modulation. This is a source-inspired COMPLETION, not a
bit-identical reconstruction of Paula hardware: upstream supplies neither
answer RTL nor the interpolation, gain, arithmetic or LFSR equations. The
rules below, not the incomplete upstream spot checks, define this task.

Retain the source 15-bit left/right input words, clk_en_sig and one-bit
left_sig/right_sig. Rename only clk_sig to clk_sig_level, carrying its actual
physical level. Input words represent signed two's-complement samples in
[-16384,16383]. All signal changes in a record occur together and settle.
Only a low-to-high clock transition with clk_en_sig=1 advances computation.
Held clocks, falling edges and disabled rising edges hold ALL computation
state and both outputs. Enable is not reset. There is no ready/valid queue.

### Four-enabled-edge interpolation

The two-bit phase starts at 0 and advances modulo 4 only on enabled rising
edges. Each channel has current interpolated sample x, target T and slope d,
all initially zero. At phase 0, latch the current input as T, compute
d=floor((T-x)/4), and use x_next=x+d. At phases 1 and 2 use x_next=x+d with
the saved slope; at phase 3 use x_next=T exactly, absorbing the remainder.
The modulator consumes x_next on this same edge. Changes to sample inputs
outside phase 0 do not affect the current interpolation interval. A pause
does not skip phases. The signed difference uses 16 bits; samples remain
within the two endpoints' 15-bit range. Division rounds toward negative
infinity, not toward zero. Gain is fixed at unity.

### Finite modulator recurrence

Each channel has signed 17-bit A1, signed 20-bit A2, and signed 15-bit F.
They start at zero. These accumulator widths complete the source's A1_WIDTH=2
and A2_WIDTH=5 as guard bits beyond DATA_WIDTH=15. Let wrap_w(v) be the
two's-complement representative of v modulo 2^w. On each enabled edge:

1. D=+1 if the OLD seed_1 bit 0 differs from OLD seed_2 bit 0, else D=-1.
2. A1_next=wrap_17(A1+x_next-F).
3. A2_next=wrap_20(A2+A1_next-F+D) for left, or with -D for right.
4. q=1 iff the wrapped A2_next is nonnegative; otherwise q=0.
5. F_next=16383 if q=1, otherwise -16384. The output bit becomes q.

All next values use the old channel state except the explicitly named
x_next and A1_next. No unbounded integer state, saturation, or unspecified
overflow is permitted. Wrapped accumulation is intentional even on sustained
full-scale input; no analog fidelity or noise-performance guarantee is claimed.

### Dither and source inconsistencies

Both 16-bit nonzero seeds advance once on each enabled edge, after their old
low bits produce D. Shift left by one, discard bit 16, and append the XOR of
old bits {15,13,12,10} for seed_1 or {15,14,12,3} for seed_2. Fresh seeds
are 1 and 2. Dither has unit amplitude and opposite channel signs.

The source CLOCK_WIDTH=2 motivates the four phases. Its READ_WIDTH=4 and
gain-related internal names have no specified equations; they are not extra
ports, tunable parameters or hidden behaviors in this completion. The source
TB incorrectly calls 0x80 the 15-bit maximum negative value (it is +128),
contains an inconsistent clock-period comment, and demands zero at isolated
timestamps without a recurrence. Those assertions cannot define the intended
one-bit modulator. We retain the stereo/interpolation/enable/error-feedback/
dither concept and publish exact executable rules instead. No unchanged
upstream-TB or original-output equivalence is claimed.


## Reset Behavior

The upstream port list has no reset pin. Fresh execution explicitly starts with zero interpolator, accumulators, feedback, phase and outputs; seed_1=1 and seed_2=2. The physical clock starts low. clk_en_sig=0 holds state and is not a reset.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
