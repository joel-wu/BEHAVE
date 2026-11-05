# Sine-windowed 32-point CELT MDCT

Transform 32 signed samples into 16 ordered modified-discrete-cosine
coefficients. The fixed full-overlap profile includes the window, folding and
transform scaling; twiddles are not arbitrary input data. It is a small
fixed-point numerical specialization of the CELT forward MDCT, not a complete
Opus encoder or a claim of bit identity with its optimized fixed-point FFT.

## Interface

### RTL interface

```systemverilog
module celt_mdct32 (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic signed [15:0] in_x0,
    input logic signed [15:0] in_x1,
    input logic signed [15:0] in_x2,
    input logic signed [15:0] in_x3,
    input logic signed [15:0] in_x4,
    input logic signed [15:0] in_x5,
    input logic signed [15:0] in_x6,
    input logic signed [15:0] in_x7,
    input logic signed [15:0] in_x8,
    input logic signed [15:0] in_x9,
    input logic signed [15:0] in_x10,
    input logic signed [15:0] in_x11,
    input logic signed [15:0] in_x12,
    input logic signed [15:0] in_x13,
    input logic signed [15:0] in_x14,
    input logic signed [15:0] in_x15,
    input logic signed [15:0] in_x16,
    input logic signed [15:0] in_x17,
    input logic signed [15:0] in_x18,
    input logic signed [15:0] in_x19,
    input logic signed [15:0] in_x20,
    input logic signed [15:0] in_x21,
    input logic signed [15:0] in_x22,
    input logic signed [15:0] in_x23,
    input logic signed [15:0] in_x24,
    input logic signed [15:0] in_x25,
    input logic signed [15:0] in_x26,
    input logic signed [15:0] in_x27,
    input logic signed [15:0] in_x28,
    input logic signed [15:0] in_x29,
    input logic signed [15:0] in_x30,
    input logic signed [15:0] in_x31,
    output logic out_valid, input logic out_ready,
    output logic signed [31:0] out_y0,
    output logic signed [31:0] out_y1,
    output logic signed [31:0] out_y2,
    output logic signed [31:0] out_y3,
    output logic signed [31:0] out_y4,
    output logic signed [31:0] out_y5,
    output logic signed [31:0] out_y6,
    output logic signed [31:0] out_y7,
    output logic signed [31:0] out_y8,
    output logic signed [31:0] out_y9,
    output logic signed [31:0] out_y10,
    output logic signed [31:0] out_y11,
    output logic signed [31:0] out_y12,
    output logic signed [31:0] out_y13,
    output logic signed [31:0] out_y14,
    output logic signed [31:0] out_y15
);
```

An input handshake accepts a complete 32-sample frame. Exactly one response
contains all 16 coefficients. Responses are ordered; valid and payload hold
under backpressure. No physical latency is prescribed. Invalid output payload
is zero.

### Behavior interface

```python
class signal_processing_audio_codec_opus_celt_mdct:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16, in_x17, in_x18, in_x19, in_x20, in_x21, in_x22, in_x23, in_x24, in_x25, in_x26, in_x27, in_x28, in_x29, in_x30, in_x31): ...
```

Call `process` once per accepted frame, passing each sample as an integer in
0..65535 encoding its signed 16-bit port bits. It returns one dictionary with
exactly the keys `out_y0`, `out_y1`, `out_y2`, `out_y3`, `out_y4`, `out_y5`,
`out_y6`, `out_y7`, `out_y8`, `out_y9`, `out_y10`, `out_y11`, `out_y12`,
`out_y13`, `out_y14`, and `out_y15`. Each value is an integer in 0..2^32-1
encoding a signed 32-bit result, not a Python float or a negative integer.
An all-zero frame returns all sixteen zero fields in this dictionary, not
`None` or sixteen separate records. There is no implicit overlap buffer:
the caller supplies complete frames, conventionally with hop 16. `reset()`
is a no-op for the memoryless transform.

## Modules

`celt_mdct32` implements a sine-windowed forward MDCT and an elastic response
buffer.

## Functional Behavior

For n=0..31, let x[n] be the signed sample. For k=0..15 define

`C[k,n] = nearest_even(2^27 * sin(pi*(n+1/2)/32)
                               * cos(pi*(n+1/2+8)*(k+1/2)/16))`.

Compute A[k]=sum(x[n]*C[k,n], n=0..31) with no intermediate narrowing. Return
the integer nearest to A[k]/2^30, with exact half ties away from zero. The
factor 2^27/2^30 supplies the forward normalization 1/8. Outputs retain signed
32-bit range rather than saturating meaningful spectral values to 16 bits.
All sums fit signed 64 bits for the full input domain. No user coefficient
can disable the transform.

The following exact Q30 table is normative (16 rows, each containing the 32
coefficients for one k), removing any platform-dependent trigonometric rounding:

```
4422721, 11731601, 16766046, 19332584, 19332584, 16766046, 11731601, 4422721, -4879720, -15818233, -27972458, -40875314, -54030952, -66933808, -79088032, -90026545, -99328986, -106637867, -111672312, -114238849, -114238849, -111672312, -106637867, -99328986, -90026545, -79088032, -66933808, -54030952, -40875314, -27972458, -15818233, -4879720
-5289725, -18542610, -32572965, -43861506, -49221162, -46338711, -34184486, -13225569, 14592166, 46092444, 77311469, 104069404, 122584698, 130038565, 125004120, 107674835, 79857100, 44727189, 6388385, -30705868, -62376800, -85300061, -97454285, -98372394, -89159541, -72277024, -51126889, -29502029, -10986735, 1600207, 6634652, 3923128
-3385753, -966330, 13943533, 36318306, 56764362, 64968143, 53693427, 21901047, -24164082, -72397205, -108392799, -120018160, -101502866, -55665703, 6514471, 68918587, 114983715, 132605102, 117695239, 75279656, 17803012, -38783563, -79878006, -96468421, -87433881, -59241567, -23245973, 8420198, 26935492, 29481123, 19670109, 5648788
5953449, 19103631, 19427093, -2218673, -38537728, -68254863, -68578325, -30365606, 33503284, 92467167, 113876359, 81481181, 6200775, -77557304, -128786222, -121185240, -57316350, 32259269, 104573919, 126219685, 89900629, 16891962, -55422687, -93635405, -84866185, -41104266, 10124653, 42519831, 45162125, 26194403, 4785211, -2815772
2218673, -10124653, -32259269, -33503284, 2815772, 55422687, 77557304, 38537728, -42519831, -104573919, -92467167, -5953449, 93635405, 128786222, 68254863, -45162125, -126219685, -113876359, -19103631, 84866185, 121185240, 68578325, -26194403, -89900629, -81481181, -19427093, 41104266, 57316350, 30365606, -4785211, -16891962, -6200775
-6388385, -13225569, 10986735, 44727189, 34184486, -29502029, -79857100, -46338711, 51126889, 107674835, 49221162, -72277024, -125004120, -43861506, 89159541, 130038565, 32572965, -98372394, -122584698, -18542610, 97454285, 104069404, 5289725, -85300061, -77311469, 3923128, 62376800, 46092444, -6634652, -30705868, -14592166, 1600207
-966330, 17803012, 21901047, -23245973, -55665703, -3385753, 75279656, 53693427, -59241567, -101502866, 5648788, 117695239, 64968143, -87433881, -120018160, 19670109, 132605102, 56764362, -96468421, -108392799, 29481123, 114983715, 36318306, -79878006, -72397205, 26935492, 68918587, 13943533, -38783563, -24164082, 8420198, 6514471
6577819, 2889685, -31634900, -15233011, 51875853, 35473964, -64219179, -60531045, 66785716, 86589539, -59184734, -109682277, 42573413, 126293598, -19480675, -133894580, -6577819, 131328043, 31634900, -118984717, -51875853, 98743764, 64219179, -73686683, -66785716, 47628189, 59184734, -24535451, -42573413, 7924130, 19480675, -323148
-323148, -19480675, 7924130, 42573413, -24535451, -59184734, 47628189, 66785716, -73686683, -64219179, 98743764, 51875853, -118984717, -31634900, 131328043, 6577819, -133894580, 19480675, 126293598, -42573413, -109682277, 59184734, 86589539, -66785716, -60531045, 64219179, 35473964, -51875853, -15233011, 31634900, 2889685, -6577819
-6514471, 8420198, 24164082, -38783563, -13943533, 68918587, -26935492, -72397205, 79878006, 36318306, -114983715, 29481123, 108392799, -96468421, -56764362, 132605102, -19670109, -120018160, 87433881, 64968143, -117695239, 5648788, 101502866, -59241567, -53693427, 75279656, 3385753, -55665703, 23245973, 21901047, -17803012, -966330
1600207, 14592166, -30705868, 6634652, 46092444, -62376800, 3923128, 77311469, -85300061, -5289725, 104069404, -97454285, -18542610, 122584698, -98372394, -32572965, 130038565, -89159541, -43861506, 125004120, -72277024, -49221162, 107674835, -51126889, -46338711, 79857100, -29502029, -34184486, 44727189, -10986735, -13225569, 6388385
6200775, -16891962, 4785211, 30365606, -57316350, 41104266, 19427093, -81481181, 89900629, -26194403, -68578325, 121185240, -84866185, -19103631, 113876359, -126219685, 45162125, 68254863, -128786222, 93635405, 5953449, -92467167, 104573919, -42519831, -38537728, 77557304, -55422687, 2815772, 33503284, -32259269, 10124653, 2218673
-2815772, -4785211, 26194403, -45162125, 42519831, -10124653, -41104266, 84866185, -93635405, 55422687, 16891962, -89900629, 126219685, -104573919, 32259269, 57316350, -121185240, 128786222, -77557304, -6200775, 81481181, -113876359, 92467167, -33503284, -30365606, 68578325, -68254863, 38537728, -2218673, -19427093, 19103631, -5953449
-5648788, 19670109, -29481123, 26935492, -8420198, -23245973, 59241567, -87433881, 96468421, -79878006, 38783563, 17803012, -75279656, 117695239, -132605102, 114983715, -68918587, 6514471, 55665703, -101502866, 120018160, -108392799, 72397205, -24164082, -21901047, 53693427, -64968143, 56764362, -36318306, 13943533, 966330, -3385753
3923128, -6634652, 1600207, 10986735, -29502029, 51126889, -72277024, 89159541, -98372394, 97454285, -85300061, 62376800, -30705868, -6388385, 44727189, -79857100, 107674835, -125004120, 130038565, -122584698, 104069404, -77311469, 46092444, -14592166, -13225569, 34184486, -46338711, 49221162, -43861506, 32572965, -18542610, 5289725
4879720, -15818233, 27972458, -40875314, 54030952, -66933808, 79088032, -90026545, 99328986, -106637867, 111672312, -114238849, 114238849, -111672312, 106637867, -99328986, 90026545, -79088032, 66933808, -54030952, 40875314, -27972458, 15818233, -4879720, -4422721, 11731601, -16766046, 19332584, -19332584, 16766046, -11731601, 4422721
```

The transform itself is memoryless. For lapped processing, adjacent input
frames share 16 samples; their overlap is caller data, not a side effect of
physical clocking. This task does not perform inverse MDCT or entropy coding.

## Reset Behavior

Synchronous active-high rst cancels a pending response, sets out_valid=0 and
all output fields to zero, and makes in_ready=1. No transform history survives
or is required.
