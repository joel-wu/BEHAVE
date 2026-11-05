# Q15 radix-4 final-stage butterfly

One transaction applies a four-input complex FFT or inverse-FFT butterfly. Pairwise sums and differences saturate before their one-bit scaling. Outputs are in natural bin order. This is a single final-stage kernel, not a complete 16-point transform.

## Interface

### RTL interface

```systemverilog
module radix4_q15_butterfly_kernel (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_ifft,
    input  logic [15:0]  in_xa_re,
    input  logic [15:0]  in_xa_im,
    input  logic [15:0]  in_xb_re,
    input  logic [15:0]  in_xb_im,
    input  logic [15:0]  in_xc_re,
    input  logic [15:0]  in_xc_im,
    input  logic [15:0]  in_xd_re,
    input  logic [15:0]  in_xd_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0_re,
    output logic [15:0]  out_x0_im,
    output logic [15:0]  out_x1_re,
    output logic [15:0]  out_x1_im,
    output logic [15:0]  out_x2_re,
    output logic [15:0]  out_x2_im,
    output logic [15:0]  out_x3_re,
    output logic [15:0]  out_x3_im
);
```

### Behavior interface

```python
class Radix4Q15ButterflyKernel:
    def __init__(self):
        ...

    def reset(self):
        ...

    def process(
        self,
        in_ifft,
        in_xa_re,
        in_xa_im,
        in_xb_re,
        in_xb_im,
        in_xc_re,
        in_xc_im,
        in_xd_re,
        in_xd_im,
    ):
        ...
```

`process` returns one dictionary containing the real and imaginary ports of
out_x0..out_x3.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0_im`, `out_x0_re`, `out_x1_im`, `out_x1_re`, `out_x2_im`, `out_x2_re`,
  `out_x3_im`, `out_x3_re`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

An input transfers on `in_valid && in_ready`, and one corresponding
output transfers on `out_valid && out_ready`. Transactions stay ordered.
While stalled, each producer holds its valid signal and complete payload
stable. Internal latency and pipeline organization are implementation choices.

## Modules

- `radix4_q15_butterfly_kernel`: the complete numerical kernel and ordered transaction interface.

## Functional Behavior

Interpret each input component as a signed 16-bit two's-complement integer.
Let `A,B,C,D` be the four complex input pairs. Define componentwise
`clip(x)=min(32767,max(-32768,x))` and `half(x)=floor(x/2)`. First compute:

```text
P = half(clip(A+C))       Q = half(clip(B+D))
R = half(clip(A-C))       S = half(clip(B-D))
```

Clipping precedes the shift on **each** real/imaginary sum or difference;
neither a wide pair sum nor saturation after the final sum is equivalent.
For `P=(pr,pi)`, `Q=(qr,qi)`, `R=(rr,ri)`, `S=(sr,si)`, outputs are:

| Lane | Forward (`in_ifft=0`) | Inverse (`in_ifft=1`) |
| --- | --- | --- |
| `out_x0` | `(pr+qr, pi+qi)` | same |
| `out_x1` | `(rr+si, ri-sr)` | `(rr-si, ri+sr)` |
| `out_x2` | `(pr-qr, pi-qi)` | same |
| `out_x3` | `(rr-si, ri+sr)` | `(rr+si, ri-sr)` |

The final sums/differences are not clipped; each component is returned modulo
`2^16`. There are no twiddles or further inverse normalization. All input bit
patterns are legal. For example, four real inputs of 32767 with zero imaginary
parts produce `out_x0_re=32766` and zero on every other component.

## Reset Behavior

`rst` is synchronous active-high and takes priority over transfers. Reset
cancels pending transactions, clears output-valid and all output payloads to
zero, and returns the module to its input-ready state. No numerical history
persists between transactions. Deasserting reset starts a fresh sequence.
