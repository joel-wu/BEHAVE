# Masked top-k softmax attention

Compute one four-dimensional query against eight keys, retain the best eligible
keys, normalize their attention weights, and return a four-dimensional weighted
value. Keys, values, masks and selection count all affect the result. This is a
fixed-size inference attention operator with dropout disabled.

## Interface

### RTL interface

```systemverilog
module exact_topk_attention (
    input logic clk,rst,
    input logic in_valid,output logic in_ready,
    input logic [31:0] in_query,
    input logic [255:0] in_keys,
    input logic [511:0] in_values,
    input logic [7:0] in_mask,
    input logic [2:0] in_keep,
    output logic out_valid,input logic out_ready,
    output logic out_status,
    output logic [7:0] out_selected,
    output logic [27:0] out_denominator,
    output logic [63:0] out_values
);
```

Every accepted request produces exactly one ordered response. All packed arrays
are least-significant-index first. Query component j is signed 8-bit at [8*j+:8].
Key i, component j is signed 8-bit at [8*(4*i+j)+:8]. Value i, component j is signed
16-bit at [16*(4*i+j)+:16]. Mask bit i=1 makes key i eligible. Keep count is
in_keep+1 (1..8). out_values packs four signed 16-bit output components.
out_selected is the selected-key bitmask; out_denominator is the unsigned Q24
normalization sum. out_status=1 means no eligible key, in which case all other
output payloads are zero; otherwise status=0. All input bit patterns are valid.
Senders hold valid and payload stable under backpressure. Latency is unspecified;
no request may be lost. Payloads are zero when out_valid=0.

### Behavior interface

```python
class ExactTopKAttention:
    def __init__(self): ...
    def reset(self): ...
    def process(self,in_query,in_keys,in_values,in_mask,in_keep): ...
```

Returns one mapping with out_status, out_selected, out_denominator and out_values,
using unsigned bit encodings.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_denominator`: 28.
- `out_selected`: 8.
- `out_status`: 1.
- `out_values`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction state

Requests do not share numerical state.

## Modules

`exact_topk_attention` computes dot products, masked selection, normalized
exponentials and weighted values with an elastic output. No selection network
or other microarchitecture is prescribed.

## Functional Behavior

Compute exact signed integer score[i]=sum_j query[j]*key[i,j]. The softmax
temperature is 1/256, so a score difference of 256 is one natural-log unit.
Select min(in_keep+1,eligible_count) keys by descending score; ties choose the
smaller input index. Ineligible keys never enter the set. Let M be its maximum.

Define R(x,d) as nearest-integer rounding of x/d, ties to even (also for negative
x), for positive integer d. The following tables define integer Q24 weights:
F[r]=R(2^24*exp(-r/256),1), r=0..255;
E[n]=R(2^24*exp(-n),1), n=0..16. Their exact integer entries are:

```
F = [16777216, 16711808, 16646655, 16581756, 16517109, 16452715, 16388572, 16324679, 16261035, 16197640, 16134491, 16071589, 16008931, 15946518, 15884349, 15822422, 15760736, 15699291, 15638085, 15577118, 15516388, 15455896, 15395639, 15335617, 15275829, 15216274, 15156952, 15097860, 15038999, 14980368, 14921965, 14863790, 14805841, 14748119, 14690621, 14633348, 14576298, 14519470, 14462864, 14406479, 14350313, 14294367, 14238638, 14183127, 14127832, 14072753, 14017889, 13963238, 13908801, 13854575, 13800561, 13746758, 13693165, 13639780, 13586604, 13533634, 13480872, 13428315, 13375963, 13323815, 13271870, 13220128, 13168588, 13117248, 13066109, 13015169, 12964428, 12913884, 12863538, 12813388, 12763433, 12713673, 12664107, 12614734, 12565554, 12516566, 12467768, 12419161, 12370743, 12322514, 12274473, 12226620, 12178953, 12131471, 12084175, 12037064, 11990136, 11943390, 11896828, 11850446, 11804246, 11758225, 11712384, 11666722, 11621238, 11575931, 11530801, 11485846, 11441067, 11396463, 11352032, 11307775, 11263690, 11219777, 11176035, 11132464, 11089063, 11045830, 11002767, 10959871, 10917143, 10874581, 10832185, 10789954, 10747888, 10705986, 10664247, 10622671, 10581257, 10540005, 10498913, 10457982, 10417210, 10376597, 10336143, 10295846, 10255706, 10215723, 10175896, 10136224, 10096706, 10057343, 10018133, 9979076, 9940172, 9901418, 9862816, 9824365, 9786063, 9747911, 9709908, 9672052, 9634345, 9596784, 9559370, 9522101, 9484978, 9447999, 9411165, 9374475, 9337927, 9301522, 9265259, 9229137, 9193156, 9157315, 9121614, 9086052, 9050629, 9015344, 8980197, 8945186, 8910312, 8875574, 8840972, 8806504, 8772171, 8737971, 8703905, 8669972, 8636171, 8602501, 8568963, 8535556, 8502279, 8469132, 8436114, 8403225, 8370464, 8337830, 8305324, 8272945, 8240692, 8208564, 8176562, 8144685, 8112932, 8081302, 8049796, 8018413, 7987152, 7956013, 7924996, 7894099, 7863323, 7832667, 7802130, 7771712, 7741413, 7711232, 7681169, 7651223, 7621394, 7591681, 7562084, 7532602, 7503235, 7473983, 7444844, 7415820, 7386908, 7358109, 7329423, 7300848, 7272385, 7244032, 7215791, 7187659, 7159637, 7131724, 7103920, 7076225, 7048637, 7021157, 6993784, 6966518, 6939358, 6912304, 6885356, 6858512, 6831773, 6805139, 6778608, 6752181, 6725856, 6699635, 6673515, 6647498, 6621582, 6595767, 6570052, 6544438, 6518924, 6493509, 6468193, 6442976, 6417857, 6392836, 6367913, 6343087, 6318357, 6293724, 6269188, 6244746, 6220400, 6196149]
E = [16777216, 6171993, 2270549, 835288, 307285, 113044, 41587, 15299, 5628, 2070, 762, 280, 103, 38, 14, 5, 2]
```

For a selected key let d=M-score[i]. Its weight is zero for d>4096;
otherwise weight[i]=R(E[d>>8]*F[d&255],2^24). Nonselected keys have zero
weight. Set D=sum_i weight[i]. The maximum has weight 2^24, so D>0 whenever
any key was selected, and D<=8*2^24 fits the declared 28-bit output.
Each output component is R(sum_i weight[i]*value[i,j],D), with signed 64-bit
intermediate sums; it lies in the signed-16 input range. There is no wrapping
or clipping of scores or weighted sums. Table rounding and the d>4096 cutoff
are the explicit finite approximation; this is not floating-point bit identity.

For one selected key, the output is exactly its value, regardless of score.
With equal scores, selected values have equal weight. Changing a value of a
nonselected or masked key cannot affect the output. An all-zero mask reports
the status above rather than an undefined all-masked softmax.

## Reset Behavior

Active-high synchronous reset discards the pending response and clears all
output registers. After reset in_ready=1, out_valid=0 and payloads are zero.
Subsequent requests are independent of all pre-reset computation.
