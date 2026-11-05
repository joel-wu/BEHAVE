# Chunked Gated Linear Attention

This block computes one complete chunked gated linear attention forward pass for a fixed-shape tensor batch. For each accepted input transaction it consumes full tensors `q`, `k`, `v`, `g`, a scalar `scale`, an initial recurrent state, and a control bit selecting whether the final recurrent state is returned. It first forms a chunk-local cumulative sum `g_cumsum`, then evaluates the recurrent inter-chunk state update and the causal intra-chunk attention contribution, and returns the output tensor `o` together with the optional final state. Primitive arithmetic uses separately rounded binary32 with preserved subnormal operands and gradual underflow. The positive, range-reduced exponential approximation is defined below; it is not a sign-based switch.

## Interface

### RTL interface

```systemverilog
module ai_ml_ssm_gla_gla_chunk #(
    parameter int B  = 1,
    parameter int T  = 1,
    parameter int H  = 1,
    parameter int K  = 1,
    parameter int V  = 1,
    parameter int BT = 16
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [31:0]                  in_scale,
    input  logic                         in_output_final_state,

    input  logic [B*T*H*K*32-1:0]        in_q,
    input  logic [B*T*H*K*32-1:0]        in_k,
    input  logic [B*T*H*V*32-1:0]        in_v,
    input  logic [B*T*H*K*32-1:0]        in_g,
    input  logic [B*H*K*V*32-1:0]        in_initial_state,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [B*T*H*V*32-1:0]        out_o,
    output logic [B*H*K*V*32-1:0]        out_final_state
);
```

Bit-vector packing is little-endian by flattened element index: element 0 occupies bits `[31:0]`, element 1 occupies bits `[63:32]`, and so on. Flattening order is row-major over the logical tensor dimensions:
- `q`, `k`, `g`: index `(((b * T) + t) * H + h) * K + d`
- `v`, `o`: index `(((b * T) + t) * H + h) * V + e`
- `initial_state`, `final_state`: index `(((b * H) + h) * K + d) * V + e`

Each 32-bit lane is an IEEE-754 binary32 bit pattern. `in_scale` is also an IEEE-754 binary32 bit pattern.

This is a 1:1 transaction interface: each accepted input transaction produces exactly one output transaction.

Legal parameter values:
- `B >= 1`
- `T >= 1`
- `H >= 1`
- `K >= 1`
- `V >= 1`
- `BT >= 1`

### Behavior interface

```python
class ChunkedGatedLinearAttention:
    def __init__(self, B=1, T=1, H=1, K=1, V=1, BT=16): ...
    def reset(self): ...
    def process(self, in_scale, in_output_final_state, in_q, in_k, in_v, in_g, in_initial_state): ...
```

`process(...)` returns one dict with keys:
- `out_o`
- `out_final_state`

`in_q`, `in_k`, `in_v`, `in_g`, `in_initial_state`, `out_o`, and `out_final_state` use the same flattened element ordering and 32-bit lane packing as the RTL ports.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_final_state`: B*H*K*V*32.
- `out_o`: B*T*H*V*32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

## Modules

- `ai_ml_ssm_gla_gla_chunk`: top-level block that performs the complete forward computation for one full tensor batch.

## Functional Behavior

Let `NT = ceil(T / BT)`. For chunk index `c`:
- `chunk_start(c) = c * BT`
- `chunk_end(c) = min(T, (c + 1) * BT)`

All interface lanes are binary32 encodings. Add, subtract and multiply round separately under the numerical contract below; exp_f32 means E under that contract. The equations fix observable rounding order, not a required chunk-storage or circuit architecture.

For each batch `b in [0, B-1]`, head `h in [0, H-1]`, time `t in [0, T-1]`, key dimension `d in [0, K-1]`, and value dimension `e in [0, V-1]`, define:
- `q[b,t,h,d]`, `k[b,t,h,d]`, `g[b,t,h,d]` from `in_q`, `in_k`, `in_g`
- `v[b,t,h,e]` from `in_v`
- `initial_state[b,h,d,e]` from `in_initial_state`
- `scale` from `in_scale`

### 1. Chunk-local cumulative sum of `g`

For each chunk `c`, for each `(b,h,d)`, the cumulative sum resets at the start of the chunk and accumulates only within that chunk:
- `s = +0.0`
- For `t` from `chunk_start(c)` to `chunk_end(c)-1`:
  - `s = f32_add(s, g[b,t,h,d])`
  - `g_cumsum[b,t,h,d] = s`

Equivalently, if `t` lies in chunk `c` and `ts = chunk_start(c)`, then `g_cumsum[b,t,h,d]` is the left-folded binary32 sum over `g[b,ts,h,d]` through `g[b,t,h,d]`. No contribution from earlier chunks is included.

### 2. Recurrent chunk state sequence

For each `(b,h)`, define a state tensor `state_c[d,e]` per chunk boundary.

Initial state before chunk 0:
- `state_0[d,e] = initial_state[b,h,d,e]`

For each chunk `c`, let `ts = chunk_start(c)`, `te = chunk_end(c)`, and `t_last = te - 1`. The state used to produce outputs for times in chunk `c` is `state_c`. The next state after chunk `c` is computed independently for every `(d,e)` as a left-folded binary32 accumulation:

- `s = f32_mul(state_c[d,e], exp_f32(g_cumsum[b,t_last,h,d]))`
- For `u` from `ts` to `te-1`:
  - `term = f32_mul(exp_f32(f32_sub(g_cumsum[b,t_last,h,d], g_cumsum[b,u,h,d])), f32_mul(k[b,u,h,d], v[b,u,h,e]))`
  - `s = f32_add(s, term)`
- `state_{c+1}[d,e] = s`

After all `NT` chunks, the architectural final state is `state_NT`.

### 3. Intra-chunk causal score matrix `A`

For each chunk `c`, define a `BT x BT` logical matrix `A_c`. Only rows and columns corresponding to valid times inside the chunk are used. Let local row `i` and local column `j` correspond to times:
- `t = ts + i`
- `u = ts + j`

For valid `0 <= i < te-ts` and `0 <= j < te-ts`:
- If `j > i`, then `A_c[i,j] = +0.0`
- If `j <= i`, then `A_c[i,j]` is computed as:
  - `a = +0.0`
  - For `d` from `0` to `K-1`:
    - `prod = f32_mul(q[b,t,h,d], k[b,u,h,d])`
    - `gate = exp_f32(f32_sub(g_cumsum[b,t,h,d], g_cumsum[b,u,h,d]))`
    - `a = f32_add(a, f32_mul(prod, gate))`
  - `A_c[i,j] = f32_mul(a, scale)`

For local positions outside the valid range `0 .. te-ts-1`, the stored value is `+0.0`.

Worked example for one `(b,h,d)` term inside a chunk: if `BT=4`, `ts=8`, `t=10`, `u=9`, then the exponential factor uses the chunk-local cumulative sums at those times, `exp_f32(f32_sub(g_cumsum[b,10,h,d], g_cumsum[b,9,h,d]))`, and does not include any `g` from times `< 8`.

### 4. Output tensor `o`

For each chunk `c`, output time `t in [ts, te-1]`, and value dimension `e`, the output is the sum of an inter-chunk recurrent term and an intra-chunk causal term.

#### 4a. Inter-chunk recurrent term

`inter[b,t,h,e]` is computed as:
- `s = +0.0`
- For `d` from `0` to `K-1`:
  - `term = f32_mul(q[b,t,h,d], exp_f32(g_cumsum[b,t,h,d]))`
  - `term = f32_mul(term, state_c[d,e])`
  - `s = f32_add(s, term)`
- `inter[b,t,h,e] = f32_mul(s, scale)`

Here `state_c` is the chunk-entry state defined in step 2, not the updated state after processing the current chunk.

#### 4b. Intra-chunk causal term

`intra[b,t,h,e]` is computed as:
- `s = +0.0`
- For `u` from `ts` to `t`:
  - `term = f32_mul(A_c[t-ts, u-ts], v[b,u,h,e])`
  - `s = f32_add(s, term)`
- `intra[b,t,h,e] = s`

Because `A_c` already includes `scale`, no second scaling is applied here.

#### 4c. Final output

`o[b,t,h,e] = f32_add(inter[b,t,h,e], intra[b,t,h,e])`

`out_o` returns the flattened binary32 bit patterns of all `o[b,t,h,e]`.

### 5. Final state output selection

If `in_output_final_state == 1`, `out_final_state` returns the flattened binary32 bit patterns of `state_NT[b,h,d,e]`.

If `in_output_final_state == 0`, every bit of `out_final_state` is zero.

### Numerical contract

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

The exponential uses range reduction and a positive piecewise-linear approximation, not a sign-based switch. Define `E2(x)` on a raw binary32 word. As an explicit approximation rule, either zero or any subnormal input returns exactly 1; this is not a rule of the basic FP32 arithmetic. For other inputs:

- NaN gives canonical NaN; negative infinity or finite x < -126 gives +0; positive infinity or x >= 128 gives +infinity.
- Otherwise q=floor(65536*x), n=floor(q/65536), f=q-65536*n, j=floor(f/2048), r=f-2048*j.
- For i=0..32, T[i] is the nearest-even integer to `2^(i/32)*2^30`. Set Y=T[j]+floor((T[j+1]-T[j])*r/2048).
- Return the nearest-even binary32 rounding of `Y*2^(n-30)`, with nearest-even rounding and signed-infinity overflow. The explicit x < -126 cutoff above belongs only to this approximation.

`E(x) = E2(mul(x, 0x3FB8AA3B))`, where the constant is binary32 log2(e). This defines both the table and the interpolation independently of either implementation. For -126 <= x < 128, E2 has relative error below 0.01% against 2^x (away from overflow); integer powers of two are exact. For E, the same 0.01% bound applies where its scaled argument is in this normal-output range. The approximation deliberately returns zero below its stated cutoff; basic FP32 arithmetic still uses gradual underflow. The task fixes observable arithmetic, not a required ROM or circuit architecture.

Chunk size BT may change last-bit rounding because sums and approximations are grouped differently; it must not remove incoming-state decay. With q=scale=1, k=v=0, g=-1 and initial_state=3, a one-token chunk returns approximately3/e as both output and final state. For g=0, the incoming state is preserved before current key/value accumulation. Tests also cover nondefault tensor shapes, multiple chunks and a final partial chunk; all positive static parameter values are part of the specified family.

Only accepted inputs are evaluated. Every accepted input has one output record, held stable under backpressure. There is no hidden recurrent state between transactions: continuation explicitly supplies the prior final state. Setting in_output_final_state=0 zeros that payload only, not the output computation.

## Reset Behavior

Reset is synchronous active-high.

On a rising edge with `rst == 1`:
- `out_valid` is deasserted to `0`
- `in_ready` becomes able to accept the next input transaction according to the handshake protocol
- `out_o` is all zeros
- `out_final_state` is all zeros

Architectural state after reset:
- no input transaction is pending
- no partial result is pending
- no retained tensor, chunk, or state data exists from before reset
