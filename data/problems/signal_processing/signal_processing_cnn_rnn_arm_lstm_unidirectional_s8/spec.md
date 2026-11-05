# Two-batch three-step quantized S8 LSTM

Evaluate a unidirectional LSTM over three timesteps for two independent batches,
with two inputs and two hidden units. All four gates, input and recurrent
matrices, nonlinear activations, cell memory and layout selection are active.
Weights and biases are request data, not an all-zero fixed network.

## Interface

### RTL interface

```systemverilog
module cmsis_s8_lstm (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_time_major,
    input logic [95:0] in_input_data,
    input logic [127:0] in_input_weights, in_hidden_weights, in_bias,
    output logic out_valid, input logic out_ready,
    output logic [7:0] out_status,
    output logic [95:0] out_output_data,
    output logic [63:0] out_cell_state
);
```

Each input handshake accepts one complete sequence and its weights. Exactly one
ordered response contains all twelve hidden outputs and the four final cell
values. Response valid and payload remain stable under backpressure. No clock
latency is prescribed; invalid output payload is zero.

### Behavior interface

```python
class CMSISS8LSTM:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_time_major, in_input_data, in_input_weights, in_hidden_weights, in_bias): ...
```

Call `process()` once per accepted complete sequence. Arguments are unsigned
Python integers encoding the declared ports; `in_time_major` is `0` or `1`.
Return one dictionary with exactly `out_status`, `out_output_data`, and
`out_cell_state`, as unsigned 8, 96, and 64-bit Python integers. `out_status`
is zero. The other fields pack all twelve signed output bytes and all four
signed final-cell words according to the layout below, with element zero in
the least-significant bits. Even an all-zero result is a full record, not
`None` or a list of timestep records. Do not call for physical gaps or stalls.
`reset()` returns `None`; no hidden or cell state is shared between requests.

## Modules

`cmsis_s8_lstm` implements the finite scalar CMSIS-NN S8/S16 LSTM profile and
an elastic response buffer. This is not a complete TensorFlow Lite runtime.

## Functional Behavior

### Layout and parameters

Packed words contain two's-complement integers; element zero occupies the
least-significant bits. No state is shared between requests.

Dimensions are T=3, B=2, I=H=2. Input/output bytes use index (t*2+b)*2+j when
time_major=1, or (b*3+t)*2+j when time_major=0. Both layouts describe the same
logical batch sequences. Input and hidden matrix entries are signed 8-bit,
packed at index g*4+j*2+k: gate g=0 forget, 1 input, 2 cell, 3 output;
j is destination and k source. Eight signed-16 biases, at g*2+j, are input
effective biases. Hidden effective biases and all zero-point offsets are zero.
Final cell state uses four signed-16 words at index b*2+j, independent of layout.

### Fixed-point operations

The normative arithmetic is the portable, default-double-rounding CMSIS-NN
v7.0.0 scalar implementation. Each gate first computes
z=SAT16(bias[g,j]+sum(W[g,j,k]*x[t,b,k])); then
z=SAT16(z+sum(U[g,j,k]*h[t-1,b,k])). The second term is zero at t=0.
These affine terms use multiplier 2^30, shift +1, giving exact unit rescaling.
Saturations occur after each matrix product, not only after their sum.

Sigmoid and tanh map signed Q3.12 inputs to signed Q0.15 outputs using the
following exact unsigned table S[i] (i=0..255):

```
32768, 33451, 34133, 34813, 35493, 36169, 36843, 37513, 38180, 38841, 39498, 40149, 40794, 41432, 42064, 42688, 43304, 43912, 44511, 45102, 45683, 46255, 46817, 47369, 47911, 48443, 48964, 49475, 49975, 50464, 50942, 51409, 51865, 52311, 52745, 53169, 53581, 53983, 54374, 54755, 55125, 55485, 55834, 56174, 56503, 56823, 57133, 57433, 57724, 58007, 58280, 58544, 58800, 59048, 59288, 59519, 59743, 59959, 60168, 60370, 60565, 60753, 60935, 61110, 61279, 61441, 61599, 61750, 61896, 62036, 62172, 62302, 62428, 62549, 62666, 62778, 62886, 62990, 63090, 63186, 63279, 63368, 63454, 63536, 63615, 63691, 63765, 63835, 63903, 63968, 64030, 64090, 64148, 64204, 64257, 64308, 64357, 64405, 64450, 64494, 64536, 64576, 64614, 64652, 64687, 64721, 64754, 64786, 64816, 64845, 64873, 64900, 64926, 64950, 64974, 64997, 65019, 65039, 65060, 65079, 65097, 65115, 65132, 65149, 65164, 65179, 65194, 65208, 65221, 65234, 65246, 65258, 65269, 65280, 65291, 65301, 65310, 65319, 65328, 65337, 65345, 65352, 65360, 65367, 65374, 65381, 65387, 65393, 65399, 65404, 65410, 65415, 65420, 65425, 65429, 65433, 65438, 65442, 65445, 65449, 65453, 65456, 65459, 65462, 65465, 65468, 65471, 65474, 65476, 65479, 65481, 65483, 65485, 65488, 65489, 65491, 65493, 65495, 65497, 65498, 65500, 65501, 65503, 65504, 65505, 65507, 65508, 65509, 65510, 65511, 65512, 65513, 65514, 65515, 65516, 65517, 65517, 65518, 65519, 65520, 65520, 65521, 65522, 65522, 65523, 65523, 65524, 65524, 65525, 65525, 65526, 65526, 65526, 65527, 65527, 65528, 65528, 65528, 65529, 65529, 65529, 65529, 65530, 65530, 65530, 65530, 65531, 65531, 65531, 65531, 65531, 65532, 65532, 65532, 65532, 65532, 65532, 65533, 65533, 65533, 65533, 65533, 65533, 65533, 65533, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65535
```

For either activation set v=3*z, a=abs(v), and use b=9 for sigmoid, b=8 for
tanh. Set i=a>>b. For i<255 interpolate r=(S[i]<<b)+(a&((1<<b)-1))*(S[i+1]-S[i]);
otherwise r=32767<<10 for sigmoid or 65535<<8 for tanh. Sigmoid returns
(r+512)>>10 for v>=0, ((1<<25)-r+511)>>10 otherwise. Tanh returns
(r-(1<<23)+128)>>8 for v>=0, ((1<<23)-r+127)>>8 otherwise. Right shifts are
arithmetic; results are signed 16-bit. This fixes lookup rounding without host
floating-point dependencies. The cell gate uses tanh, all other gates sigmoid.

Define Q(x,s): first v=(x+1)>>1; then round v/2^s to nearest integer, with
exact half ties away from zero. This is CMSIS requantization with multiplier
2^30 and shift -s, including its two rounding stages. Products fit signed32.

### Recurrent update

Start h[-1,b,j]=0 and c[-1,b,j]=0 for each request. For each timestep compute
all gates using the old hidden vector before updating either hidden unit:

1. f=sigmoid(z_forget), i=sigmoid(z_input), g=tanh(z_cell), o=sigmoid(z_output).
2. c[t,b,j]=CLAMP(SAT16(Q(f*c[t-1,b,j],14))+Q(i*g,17), -32767,32767).
3. h[t,b,j]=SAT8(Q(o*tanh(c[t,b,j]),22)).

Cell scale is 2^-12, so its tanh uses the same Q3.12 input scale. Hidden output
scale is 2^-7. Return every h in the selected layout and final c in batch order.
Inputs, recurrent weights, each gate and earlier timesteps can all affect the
result. A new request starts a new sequence, matching the source wrapper's
cell-state initialization; physical gaps and stalls never act as timesteps.

## Reset Behavior

Synchronous active-high reset discards a pending response, clears output valid
and every payload, and makes input ready. Requests after reset start with zero
hidden and cell state, just as any other new sequence.
