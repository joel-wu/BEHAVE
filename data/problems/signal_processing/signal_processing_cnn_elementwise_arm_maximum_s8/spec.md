# SignalProcessingCnnElementwiseArmMaximumS8

This block computes the signed int8 maximum at a selected coordinate of two
NHWC tensors with per-axis broadcasting. Each command carries both complete
packed tensors, their shapes and one output coordinate. The block performs
broadcast address generation, selects the two input elements and returns
their maximum. Each axis supports extents 1 through 4, including independent
broadcasting along batch, height, width and channel.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_elementwise_arm_maximum_s8 (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    output logic          in_ready,
    input  logic [2:0] in_input_1_n,
    input  logic [2:0] in_input_1_h,
    input  logic [2:0] in_input_1_w,
    input  logic [2:0] in_input_1_c,
    input  logic [2:0] in_input_2_n,
    input  logic [2:0] in_input_2_h,
    input  logic [2:0] in_input_2_w,
    input  logic [2:0] in_input_2_c,
    input  logic [1:0] in_out_n,
    input  logic [1:0] in_out_h,
    input  logic [1:0] in_out_w,
    input  logic [1:0] in_out_c,
    input  logic [2047:0] in_input_1_data,
    input  logic [2047:0] in_input_2_data,
    output logic          out_valid,
    input  logic          out_ready,
    output logic [7:0]    out_value,
    output logic          out_error
);
```

- `in_input_1_n/h/w/c` and `in_input_2_n/h/w/c` are unsigned extents.
- `in_out_n/h/w/c` are unsigned coordinates, not output extents.
- Each `in_input_*_data` holds up to 256 signed int8 elements. Byte zero
  occupies bits [7:0]; subsequent elements occupy successively higher bytes.
  Elements are compact NHWC row-major, channel fastest. Bytes beyond the
  shape's element count are ignored.
- `out_value` is the raw two's-complement maximum. `out_error` reports an
  invalid shape or coordinate.
- Each input accepted at a rising edge with `in_valid && in_ready` produces
  exactly one ordered output. An output transfers with `out_valid && out_ready`.
  Valid payloads remain stable until accepted, including under backpressure.
- `rst` is synchronous active-high. Latency is not fixed.

### Behavior interface

```python
class SignalProcessingCnnElementwiseArmMaximumS8:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_input_1_n, in_input_1_h, in_input_1_w, in_input_1_c, in_input_2_n, in_input_2_h, in_input_2_w, in_input_2_c, in_out_n, in_out_h, in_out_w, in_out_c, in_input_1_data, in_input_2_data): ...
```

Each `process` call represents one accepted request and returns exactly
`{"out_value": raw_uint8, "out_error": uint1}`. Extents are unsigned 3-bit
integers, coordinates unsigned 2-bit integers, and each packed tensor an
unsigned 2048-bit word. `out_value` encodes the signed-int8 result. An invalid
shape or coordinate returns `{"out_value": 0, "out_error": 1}`, not an
exception, `None`, or a list. Valid zero results also return both fields.
All tensor data is supplied anew on each call; there is no persistent state.
Construction and `reset()` have no numerical effect; `reset()` returns `None`.
Idle and output-stall cycles require no calls.

## Modules

- `signal_processing_cnn_elementwise_arm_maximum_s8`: bounded four-axis
  broadcast indexing and signed maximum.

## Functional Behavior

For each axis, let the two extents be `a,b` and the output coordinate be
`q`. A command is valid exactly when all four axes satisfy:

- `1 <= a <= 4` and `1 <= b <= 4`;
- `a == b`, `a == 1`, or `b == 1`;
- `q < max(a,b)`.

For an invalid command, return `out_error=1,out_value=0`; no tensor read is
required. Otherwise return `out_error=0` and compute the following.

For input tensor `r`, each selected coordinate is zero when that tensor's
corresponding extent is one; otherwise it is the supplied output coordinate.
If these coordinates are `nr,hr,wr,cr`, the selected linear element index is

`index_r = ((nr*H_r + hr)*W_r + wr)*C_r + cr`.

Decode byte `index_r` of `in_input_r_data` as a signed int8 value.
Return the larger of the two signed values, with no arithmetic rescaling,
rounding or saturation. Equal values yield that same value.

For example, input 1 with shape (1,1,1,2) and elements [-3,7], and input 2
with shape (1,2,1,1) and elements [4,-8], produce output shape (1,2,1,2).
The four coordinate results in NHWC order are [4,7,-3,7].
A negative byte (for example 0xFF=-1) never outranks positive 0x01.

Requests are independent. Modifying a shape or coordinate changes which
tensor bytes are read; modifying an unused padding byte cannot change
a valid result.

## Reset Behavior

On a rising edge with `rst=1`, clear pending output, validity, value and
error. The reset state has `in_ready=1`. Reset cancels any accepted command
whose response has not transferred; no pre-reset response may reappear.
There is no retained tensor or algorithmic state between commands.
