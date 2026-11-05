# Unsigned-Byte L2 Distance D64

This block computes exact squared Euclidean distance between two vectors of 64 unsigned bytes. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module l2_distance_uint8_d64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_a0,
    input  logic [7:0]   in_a1,
    input  logic [7:0]   in_a2,
    input  logic [7:0]   in_a3,
    input  logic [7:0]   in_a4,
    input  logic [7:0]   in_a5,
    input  logic [7:0]   in_a6,
    input  logic [7:0]   in_a7,
    input  logic [7:0]   in_a8,
    input  logic [7:0]   in_a9,
    input  logic [7:0]   in_a10,
    input  logic [7:0]   in_a11,
    input  logic [7:0]   in_a12,
    input  logic [7:0]   in_a13,
    input  logic [7:0]   in_a14,
    input  logic [7:0]   in_a15,
    input  logic [7:0]   in_a16,
    input  logic [7:0]   in_a17,
    input  logic [7:0]   in_a18,
    input  logic [7:0]   in_a19,
    input  logic [7:0]   in_a20,
    input  logic [7:0]   in_a21,
    input  logic [7:0]   in_a22,
    input  logic [7:0]   in_a23,
    input  logic [7:0]   in_a24,
    input  logic [7:0]   in_a25,
    input  logic [7:0]   in_a26,
    input  logic [7:0]   in_a27,
    input  logic [7:0]   in_a28,
    input  logic [7:0]   in_a29,
    input  logic [7:0]   in_a30,
    input  logic [7:0]   in_a31,
    input  logic [7:0]   in_a32,
    input  logic [7:0]   in_a33,
    input  logic [7:0]   in_a34,
    input  logic [7:0]   in_a35,
    input  logic [7:0]   in_a36,
    input  logic [7:0]   in_a37,
    input  logic [7:0]   in_a38,
    input  logic [7:0]   in_a39,
    input  logic [7:0]   in_a40,
    input  logic [7:0]   in_a41,
    input  logic [7:0]   in_a42,
    input  logic [7:0]   in_a43,
    input  logic [7:0]   in_a44,
    input  logic [7:0]   in_a45,
    input  logic [7:0]   in_a46,
    input  logic [7:0]   in_a47,
    input  logic [7:0]   in_a48,
    input  logic [7:0]   in_a49,
    input  logic [7:0]   in_a50,
    input  logic [7:0]   in_a51,
    input  logic [7:0]   in_a52,
    input  logic [7:0]   in_a53,
    input  logic [7:0]   in_a54,
    input  logic [7:0]   in_a55,
    input  logic [7:0]   in_a56,
    input  logic [7:0]   in_a57,
    input  logic [7:0]   in_a58,
    input  logic [7:0]   in_a59,
    input  logic [7:0]   in_a60,
    input  logic [7:0]   in_a61,
    input  logic [7:0]   in_a62,
    input  logic [7:0]   in_a63,
    input  logic [7:0]   in_b0,
    input  logic [7:0]   in_b1,
    input  logic [7:0]   in_b2,
    input  logic [7:0]   in_b3,
    input  logic [7:0]   in_b4,
    input  logic [7:0]   in_b5,
    input  logic [7:0]   in_b6,
    input  logic [7:0]   in_b7,
    input  logic [7:0]   in_b8,
    input  logic [7:0]   in_b9,
    input  logic [7:0]   in_b10,
    input  logic [7:0]   in_b11,
    input  logic [7:0]   in_b12,
    input  logic [7:0]   in_b13,
    input  logic [7:0]   in_b14,
    input  logic [7:0]   in_b15,
    input  logic [7:0]   in_b16,
    input  logic [7:0]   in_b17,
    input  logic [7:0]   in_b18,
    input  logic [7:0]   in_b19,
    input  logic [7:0]   in_b20,
    input  logic [7:0]   in_b21,
    input  logic [7:0]   in_b22,
    input  logic [7:0]   in_b23,
    input  logic [7:0]   in_b24,
    input  logic [7:0]   in_b25,
    input  logic [7:0]   in_b26,
    input  logic [7:0]   in_b27,
    input  logic [7:0]   in_b28,
    input  logic [7:0]   in_b29,
    input  logic [7:0]   in_b30,
    input  logic [7:0]   in_b31,
    input  logic [7:0]   in_b32,
    input  logic [7:0]   in_b33,
    input  logic [7:0]   in_b34,
    input  logic [7:0]   in_b35,
    input  logic [7:0]   in_b36,
    input  logic [7:0]   in_b37,
    input  logic [7:0]   in_b38,
    input  logic [7:0]   in_b39,
    input  logic [7:0]   in_b40,
    input  logic [7:0]   in_b41,
    input  logic [7:0]   in_b42,
    input  logic [7:0]   in_b43,
    input  logic [7:0]   in_b44,
    input  logic [7:0]   in_b45,
    input  logic [7:0]   in_b46,
    input  logic [7:0]   in_b47,
    input  logic [7:0]   in_b48,
    input  logic [7:0]   in_b49,
    input  logic [7:0]   in_b50,
    input  logic [7:0]   in_b51,
    input  logic [7:0]   in_b52,
    input  logic [7:0]   in_b53,
    input  logic [7:0]   in_b54,
    input  logic [7:0]   in_b55,
    input  logic [7:0]   in_b56,
    input  logic [7:0]   in_b57,
    input  logic [7:0]   in_b58,
    input  logic [7:0]   in_b59,
    input  logic [7:0]   in_b60,
    input  logic [7:0]   in_b61,
    input  logic [7:0]   in_b62,
    input  logic [7:0]   in_b63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [21:0]  out_distance
);
```

### Behavior interface

```python
class L2DistanceUint8D64:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_a16, in_a17, in_a18, in_a19, in_a20, in_a21, in_a22, in_a23,
        in_a24, in_a25, in_a26, in_a27, in_a28, in_a29, in_a30, in_a31,
        in_a32, in_a33, in_a34, in_a35, in_a36, in_a37, in_a38, in_a39,
        in_a40, in_a41, in_a42, in_a43, in_a44, in_a45, in_a46, in_a47,
        in_a48, in_a49, in_a50, in_a51, in_a52, in_a53, in_a54, in_a55,
        in_a56, in_a57, in_a58, in_a59, in_a60, in_a61, in_a62, in_a63,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
        in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
        in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
        in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
        in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
        in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
        in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63
    ): ...
```

Each call consumes one accepted pair of complete vectors and returns one `dict`
with exactly `out_distance`, a Python integer in 0..4161600. Every input element
is an integer in 0..255. Identical vectors return `{"out_distance": 0}`, never
`None` or an empty list. The result is squared distance, not a square root or
a sequence of coordinate results. There is no algorithmic history between calls;
`reset()` produces no response.

## Modules

- `l2_distance_uint8_d64`: top-level transaction operator.

## Functional Behavior

Transfers occur on rising edges with valid and ready both high. Output payloads remain valid and unchanged under backpressure. The block supports ordered responses and simultaneous output consumption and input acceptance.

All 128 payload ports `in_a0..in_a63` and `in_b0..in_b63` are unsigned eight-bit integers. Compute `out_distance = sum((a[i]-b[i])^2, i=0..63)` using widened subtraction and exact accumulation. There is no signed-byte reinterpretation, scale, rounding or saturation.

Each difference lies in [-255,255]; the total lies in [0,4161600] and fits the unsigned 22-bit output. Identical vectors yield zero. A single 0-versus-255 coordinate contributes 65025. All-zero versus all-255 vectors yield 4161600.

## Reset Behavior

Reset is synchronous active-high `rst`. It cancels any pending response, clears `out_valid` and all output payloads to zero. There is no retained algorithmic state. New requests can be accepted after reset is released.
