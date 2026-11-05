# mLSTM Recurrent Matrix-Memory Core

Perform one stabilized mLSTM recurrence with four query/key channels and two
value channels. Each request supplies projected Q/K/V, input/forget gates and
an optional previous state (matrix C, vector N, scalar M); the response contains
the normalized two-channel output and all three updated states. Feed those
states into the next request to process a sequence. This is the recurrent core,
not a full xLSTM block: projections, layer normalization, residuals and the
feed-forward network are outside its boundary.

## Interface

### RTL interface

```systemverilog
module MLSTMRecurrentCore (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_query,
    input logic [63:0] in_key,
    input logic [31:0] in_value,
    input logic [7:0] in_igate,
    input logic [7:0] in_fgate,
    input logic in_state_valid,
    input logic [255:0] in_cstate,
    input logic [127:0] in_nstate,
    input logic [15:0] in_mstate,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_h,
    output logic [255:0] out_cstate,
    output logic [127:0] out_nstate,
    output logic [15:0] out_mstate,
    output logic out_error
);
```

### Behavior interface

```python
class MLSTMRecurrentCore:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_query, in_key, in_value, in_igate, in_fgate,
                in_state_valid, in_cstate, in_nstate, in_mstate): ...
```

The return mapping contains `out_h`, `out_cstate`, `out_nstate`, `out_mstate`
and `out_error`, as nonnegative port-word integers.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_cstate`: 256.
- `out_error`: 1.
- `out_h`: 32.
- `out_mstate`: 16.
- `out_nstate`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

| Ports | Lane representation | Layout |
| --- | --- | --- |
| query, key | signed 16-bit Q8.8 | four lanes, lane r at bits 16*r +: 16 |
| value, out_h | signed 16-bit Q8.8 | two lanes, lane j at bits 16*j +: 16 |
| cstate, out_cstate | signed 32-bit Q16.16 | 4-by-2 matrix, lane 2*r+j at bits 32*(2*r+j) +: 32 |
| nstate, out_nstate | signed 32-bit Q16.16 | four lanes, lane r at bits 32*r +: 32 |
| igate, fgate | signed 8-bit Q4.4 | scalar |
| mstate, out_mstate | signed 16-bit word divided by 16 | scalar; admitted state range [-8,8] |

Signed fields use two's complement. With `in_state_valid=0`, ignore all supplied
C/N/M words and start from zeros. With it set, M's integer word must be in
[-128,128]; otherwise return `out_error=1` and every other output word zero.
All other port encodings are legal. Normal responses have `out_error=0`.

Requests transfer on rising edges outside reset when `in_valid && in_ready`,
responses when `out_valid && out_ready`. Keep offered input stable while waiting;
keep a valid response stable under backpressure. Every accepted request produces
exactly one ordered response unless reset discards it. Latency is not prescribed.

## Modules

`MLSTMRecurrentCore` implements the numerical cell and transport. Recurrent
state is explicit in the request/response, not hidden between independent calls.

## Functional Behavior

The following formulas use signed integer lane words and unbounded intermediate
arithmetic. `floor` rounds toward negative infinity. `sat32` clamps to
[-2^31,2^31-1], and `sat16` clamps to [-32768,32767]. No other overflow, rounding
or clipping is implicit. Let C,N,M be the selected old state, i the input-gate
word and f the forget-gate word.

1. `l = L[f+128]`, `Mnew=max(M+l,i)`.
2. `F=E(M+l-Mnew)`, `I=E(i-Mnew)`.
3. `Cnew[r,j]=sat32(floor((F*C[r,j]+I*K[r]*V[j])/65536))`.
4. `Nnew[r]=sat32(floor((F*N[r]+I*K[r]*256)/65536))`.
5. `d=max(abs(floor(sum_r Q[r]*Nnew[r]/512)),E(-Mnew))+1`.
6. `h[j]=sat16(floor(256*floor(sum_r Q[r]*Cnew[r,j]/512)/d))`.

The factor 512 combines Q8.8 scaling with the source's query scale
1/sqrt(4)=1/2. The denominator retains both the absolute query/normalizer dot
product and the stabilized lower bound exp(-Mnew); its final `+1` is epsilon
1/65536 in Q16.16. It is strictly positive. Cnew is a key-by-value outer-product
memory, not its transpose. Mnew stays in [-128,128], so a returned state is always
admissible for the next request. Pack h, Cnew, Nnew and Mnew modulo their lane
widths after the specified saturation.

L is rounded log-sigmoid: `L[f+128]=floor(-16*ln(1+exp(-f/16))+0.5)`.
Its exact words in f=-128..127 order are:

```text
-128, -127, -126, -125, -124, -123, -122, -121, -120, -119, -118, -117
-116, -115, -114, -113, -112, -111, -110, -109, -108, -107, -106, -105
-104, -103, -102, -101, -100, -99, -98, -97, -96, -95, -94, -93
-92, -91, -90, -89, -88, -87, -86, -85, -84, -83, -82, -81
-80, -79, -78, -77, -76, -75, -74, -73, -72, -71, -70, -69
-68, -67, -66, -65, -64, -63, -62, -61, -60, -59, -58, -57
-56, -56, -55, -54, -53, -52, -51, -50, -49, -48, -47, -46
-45, -44, -43, -42, -41, -40, -39, -39, -38, -37, -36, -35
-34, -33, -32, -31, -31, -30, -29, -28, -27, -26, -26, -25
-24, -23, -22, -22, -21, -20, -20, -19, -18, -18, -17, -16
-16, -15, -14, -14, -13, -13, -12, -12, -11, -11, -10, -10
-9, -9, -8, -8, -8, -7, -7, -7, -6, -6, -6, -5
-5, -5, -4, -4, -4, -4, -4, -3, -3, -3, -3, -3
-3, -2, -2, -2, -2, -2, -2, -2, -2, -2, -1, -1
-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1
-1, -1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0
```

For integer t, `E(t)=floor(65536*exp(t/16)+0.5)`. All calls have t<=128.
For t<-188 its value is zero; the exact words for t=-188..128 follow:

```text
1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1
1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2
2, 2, 3, 3, 3, 3, 3, 4, 4, 4, 4, 5
5, 5, 6, 6, 6, 7, 7, 8, 8, 9, 9, 10
10, 11, 12, 13, 13, 14, 15, 16, 17, 18, 19, 21
22, 23, 25, 27, 28, 30, 32, 34, 36, 39, 41, 44
47, 50, 53, 56, 60, 64, 68, 72, 77, 82, 87, 93
99, 105, 112, 119, 127, 135, 143, 153, 162, 173, 184, 196
209, 222, 236, 252, 268, 285, 303, 323, 344, 366, 390, 415
442, 470, 500, 533, 567, 604, 642, 684, 728, 775, 825, 878
935, 995, 1059, 1128, 1200, 1278, 1360, 1448, 1541, 1641, 1746, 1859
1979, 2107, 2243, 2387, 2541, 2705, 2879, 3065, 3263, 3473, 3697, 3936
4190, 4460, 4747, 5054, 5380, 5726, 6096, 6489, 6907, 7353, 7827, 8332
8869, 9441, 10050, 10698, 11388, 12123, 12905, 13737, 14623, 15566, 16570, 17639
18776, 19987, 21276, 22649, 24109, 25664, 27319, 29081, 30957, 32954, 35079, 37341
39750, 42313, 45042, 47947, 51039, 54331, 57835, 61565, 65536, 69763, 74262, 79052
84150, 89577, 95354, 101504, 108051, 115019, 122437, 130334, 138740, 147688, 157213, 167352
178145, 189635, 201865, 214884, 228743, 243496, 259200, 275917, 293712, 312655, 332819, 354284
377134, 401457, 427348, 454910, 484249, 515481, 548726, 584116, 621788, 661890, 704578, 750020
798392, 849884, 904697, 963044, 1025156, 1091272, 1161653, 1236574, 1316326, 1401221, 1491592, 1587792
1690196, 1799204, 1915243, 2038765, 2170254, 2310224, 2459220, 2617826, 2786662, 2966386, 3157701, 3361356
3578144, 3808915, 4054569, 4316066, 4594428, 4890743, 5206169, 5541938, 5899363, 6279839, 6684854, 7115990
7574932, 8063473, 8583522, 9137112, 9726405, 10353704, 11021461, 11732284, 12488951, 13294419, 14151835, 15064550
16036130, 17070372, 18171316, 19343266, 20590799, 21918792, 23332432, 24837245, 26439109, 28144285, 29959436, 31891654
33948488, 36137978, 38468677, 40949694, 43590722, 46402082, 49394759, 52580448, 55971595, 59581453, 63424126, 67514631
71868951, 76504100, 81438190, 86690502, 92281559, 98233209, 104568706, 111312808, 118491868, 126133937, 134268877, 142928475
152146570, 161959180, 172404650, 183523795, 195360063
```

These tables, floors and saturation define the fixed-point task; they do not
claim binary32 equivalence to a library implementation. In particular, retain M
when chaining calls: dropping it changes the denominator and epsilon scaling.

Example: start from zero state, Q=K=(256,0,0,0), V=(512,-256), i=f=0.
Then Mnew=0, I=65536, Cnew's first row is (131072,-65536), its other rows are
zero, and Nnew=(65536,0,0,0). The denominator is 65537, and h=(255,-128).
The negative output uses floor, not truncation toward zero.

## Reset Behavior

Reset is synchronous active-high. Asserted reset forces `in_ready=0`; a reset
edge discards pending work, clears `out_valid` and sets every output word to
zero. The next request is independent of discarded work. Output data is otherwise unspecified
while `out_valid=0`.
