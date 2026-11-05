# Stable UINT32 Argsort, Eight Keys

Return the original indices of eight unsigned 32-bit keys in ascending order.
Equal keys retain their input-index order. The required result is a permutation,
not a prescribed radix or comparison-network implementation.

## Interface

### RTL interface

```systemverilog
module RadixSortMultipass8b (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_key_0,
    input  logic [31:0] in_key_1,
    input  logic [31:0] in_key_2,
    input  logic [31:0] in_key_3,
    input  logic [31:0] in_key_4,
    input  logic [31:0] in_key_5,
    input  logic [31:0] in_key_6,
    input  logic [31:0] in_key_7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_rank_0,
    output logic [2:0]  out_rank_1,
    output logic [2:0]  out_rank_2,
    output logic [2:0]  out_rank_3,
    output logic [2:0]  out_rank_4,
    output logic [2:0]  out_rank_5,
    output logic [2:0]  out_rank_6,
    output logic [2:0]  out_rank_7
);
```

### Behavior interface

```python
class RadixSortMultipass8b:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_key_0,
        in_key_1,
        in_key_2,
        in_key_3,
        in_key_4,
        in_key_5,
        in_key_6,
        in_key_7,
    ):
        pass
```

The return value of `process(...)` is one dict with keys:
`out_rank_0`, `out_rank_1`, `out_rank_2`, `out_rank_3`,
`out_rank_4`, `out_rank_5`, `out_rank_6`, `out_rank_7`.

Each call represents one accepted group of eight unsigned 32-bit keys. All
eight returned values are unsigned 3-bit indices. The result is a single
complete dictionary, not sorted key values, a list, or one response per key.
Repeated and all-equal inputs also return all eight fields. Calls do not
represent waiting cycles. There is no cross-request sorting state; `reset()`
returns `None` and emits no response.

## Modules

- `RadixSortMultipass8b`: top-level module implementing stable eight-key argsort.

## Functional Behavior

One accepted input transaction produces exactly one output transaction.

Each input `in_key_i` is an unsigned 32-bit integer in the range `[0, 2^32 - 1]`.

Define the initial record sequence for one transaction as eight pairs:
- `r0 = (key=in_key_0, index=0)`
- `r1 = (key=in_key_1, index=1)`
- `r2 = (key=in_key_2, index=2)`
- `r3 = (key=in_key_3, index=3)`
- `r4 = (key=in_key_4, index=4)`
- `r5 = (key=in_key_5, index=5)`
- `r6 = (key=in_key_6, index=6)`
- `r7 = (key=in_key_7, index=7)`

One equivalent algorithm applies four stable sorting passes; it is not a required microarchitecture. Pass `p` uses digit

- `digit_p(record) = (record.key >> (8 * p)) & 8'hFF`

with:
- pass 0: bits `[7:0]`
- pass 1: bits `[15:8]`
- pass 2: bits `[23:16]`
- pass 3: bits `[31:24]`

A stable sorting pass reorders the eight records into ascending order of `digit_p`. If two records have equal `digit_p`, their relative order after that pass is the same as their relative order before that pass.

The four passes are applied sequentially:
1. stable sort by byte 0
2. stable sort the result by byte 1
3. stable sort the result by byte 2
4. stable sort the result by byte 3

After pass 3, the final record order is the ascending unsigned 32-bit numeric order of the eight keys. If two or more input keys are equal, they remain in original input-index order. Therefore the final comparison rule is lexicographic on `(key, index)`, with `key` compared first as unsigned 32-bit magnitude and `index` compared second as unsigned 3-bit magnitude.

Let the final ordered records be `s[0]` through `s[7]`. The output ports are:
- `out_rank_0 = s[0].index`
- `out_rank_1 = s[1].index`
- `out_rank_2 = s[2].index`
- `out_rank_3 = s[3].index`
- `out_rank_4 = s[4].index`
- `out_rank_5 = s[5].index`
- `out_rank_6 = s[6].index`
- `out_rank_7 = s[7].index`

Each `out_rank_i` is a 3-bit unsigned value. Across one output transaction, the eight outputs form a permutation of `0,1,2,3,4,5,6,7`.

Equivalent direct definition: the output list is the list of original indices sorted by `(in_key_i, i)` ascending.

Worked example 1:
- inputs: `in_key_0=0x00000005`, `in_key_1=0x00000002`, `in_key_2=0x00000007`, `in_key_3=0x00000002`,
  `in_key_4=0x00000009`, `in_key_5=0x00000001`, `in_key_6=0x00000005`, `in_key_7=0x00000003`
- ascending `(key,index)` order is:
  `(1,5), (2,1), (2,3), (3,7), (5,0), (5,6), (7,2), (9,4)`
- outputs:
  `out_rank_0=5, out_rank_1=1, out_rank_2=3, out_rank_3=7,
   out_rank_4=0, out_rank_5=6, out_rank_6=2, out_rank_7=4`

Worked example 2:
- inputs: all eight keys equal to `0x12345678`
- every pass preserves the existing order because all digits tie
- outputs:
  `out_rank_0=0, out_rank_1=1, out_rank_2=2, out_rank_3=3,
   out_rank_4=4, out_rank_5=5, out_rank_6=6, out_rank_7=7`

No signed interpretation is used anywhere. No saturation, wraparound arithmetic, or out-of-range behavior applies beyond the 32-bit input and 3-bit output widths already stated.

### Source boundary

Faiss `fvec_argsort` initializes a permutation and sorts its indices by value.
This task retains the argsort result, using unsigned 32-bit keys instead of
float values and adding an explicit stable index tie-break. It does not claim
to reproduce unspecified equal-key order in `std::sort`, a Faiss radix kernel,
or a particular number of cycles. The existing comparison-network RTL and a
radix implementation may both satisfy the same transaction contract.

## Reset Behavior

The reset input is active-high. Reset may be implemented synchronously or asynchronously. After reset is asserted, and until the first non-reset accepted input transaction has been processed, the architectural state is empty and no result is pending.

Architectural state after reset:
- no buffered input transaction
- no buffered output transaction pending

Output port values after reset:
- `out_valid = 0`
- `out_rank_0 = 0`
- `out_rank_1 = 0`
- `out_rank_2 = 0`
- `out_rank_3 = 0`
- `out_rank_4 = 0`
- `out_rank_5 = 0`
- `out_rank_6 = 0`
- `out_rank_7 = 0`

The module may accept a new input transaction whenever it has no unconsumed output transaction pending. Since output cardinality is 1:1, at most one output transaction needs to be buffered internally.
