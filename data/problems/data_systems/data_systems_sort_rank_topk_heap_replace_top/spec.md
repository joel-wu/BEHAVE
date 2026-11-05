# HeapReplaceTop

This module performs one in-place replacement of the root element of a fixed-size binary heap that stores `K` `(value, id)` pairs. The heap ordering is a max-heap over lexicographically ordered pairs `(value, id)`, where a pair is considered greater when it has a larger `value`, or when `value` is equal and it has a larger `id`. On each accepted transaction, the module receives the current heap contents and one replacement pair, overwrites the heap root with the new pair, restores the heap property by sifting the new pair downward, and returns the updated heap contents.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_topk_heap_replace_top #(
    parameter int unsigned K = 8,
    parameter int unsigned VALUE_W = 32,
    parameter int unsigned ID_W = 64
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [K*VALUE_W-1:0]         in_bh_val,
    input  logic [K*ID_W-1:0]            in_bh_ids,
    input  logic [VALUE_W-1:0]           in_val,
    input  logic [ID_W-1:0]              in_id,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [K*VALUE_W-1:0]         out_bh_val,
    output logic [K*ID_W-1:0]            out_bh_ids
);
```

`in_bh_val` and `out_bh_val` pack heap entries in index order `0 .. K-1`, least-significant slice first:
- entry `j` value bits are `[(j+1)*VALUE_W-1 : j*VALUE_W]`

`in_bh_ids` and `out_bh_ids` pack heap IDs in index order `0 .. K-1`, least-significant slice first:
- entry `j` ID bits are `[(j+1)*ID_W-1 : j*ID_W]`

All comparisons treat `value` and `id` as unsigned integers.

`K` shall be at least 1.

### Behavior interface

```python
class HeapReplaceTop:
    def __init__(self, K=8, VALUE_W=32, ID_W=64):
        pass

    def reset(self):
        pass

    def process(self, in_bh_val, in_bh_ids, in_val, in_id):
        pass
```

One call represents one accepted heap-replacement request. Pass `in_bh_val`,
`in_bh_ids`, `in_val` and `in_id` as unsigned integer encodings of widths
`K*VALUE_W`, `K*ID_W`, `VALUE_W` and `ID_W`, respectively, not Python arrays.
Every call returns exactly one dictionary with `out_bh_val` and `out_bh_ids`,
unsigned encodings of widths `K*VALUE_W` and `K*ID_W` in the packing above.
Zero values still produce the full dictionary, never `None` or a list.
The heap is supplied anew on each call; it is not retained model state.
Construction selects the parameters. `reset()` preserves those parameters,
returns `None` and emits no output record.

## Modules

- `data_systems_sort_rank_topk_heap_replace_top`: top-level heap root replacement block for one max-heap.
- No helper modules are required by the specification.

## Functional Behavior

Let the input heap arrays be:
- `bh_val[0 .. K-1]`, unpacked from `in_bh_val`
- `bh_ids[0 .. K-1]`, unpacked from `in_bh_ids`

Let the replacement pair be:
- `val = in_val`
- `id = in_id`

All stored and returned values are reduced modulo their port widths:
- every heap value is `VALUE_W` bits
- every heap ID is `ID_W` bits

The operation is defined by the following max-heap comparator over pairs `(a_val, a_id)` and `(b_val, b_id)`:

`cmp2(a_val, b_val, a_id, b_id)` is true iff:
- `a_val > b_val`, or
- `a_val == b_val` and `a_id > b_id`

This comparator is used both to choose the larger child and to test whether the candidate pair has reached a valid heap position.

The accepted input transaction performs exactly one root replacement on the `K`-element heap:

1. Unpack the input arrays into zero-based arrays `bh_val[]` and `bh_ids[]`.
2. Initialize:
   - `i = 0`
   - `val = in_val`
   - `id = in_id`
3. Repeatedly examine the children of node `i`:
   - left child index `i1 = 2*i + 1`
   - right child index `i2 = 2*i + 2`
4. If `i1 >= K`, node `i` has no children, so stop the search.
5. Otherwise choose the larger child:
   - if `i2 >= K`, choose child `i1`
   - else if `cmp2(bh_val[i1], bh_val[i2], bh_ids[i1], bh_ids[i2])` is true, choose child `i1`
   - else choose child `i2`
6. Let the chosen child index be `c`. Then:
   - if `cmp2(val, bh_val[c], id, bh_ids[c])` is true, stop the search
   - else move the chosen child up one level:
     - `bh_val[i] = bh_val[c]`
     - `bh_ids[i] = bh_ids[c]`
     - `i = c`
     - continue from step 3
7. After the search stops, write the replacement pair into the final position:
   - `bh_val[i] = val`
   - `bh_ids[i] = id`
8. Pack the updated arrays back into `out_bh_val` and `out_bh_ids` using the same least-significant-slice-first layout as the inputs.

Special case `K = 1`:
- the loop terminates immediately because the root has no children
- the output heap is exactly one entry containing `(in_val, in_id)`

The module does not validate that the input arrays already satisfy the max-heap property. The output is defined as the result of the above procedure for any bit pattern on the inputs.

Worked example for `K = 4`:

Input heap:
- index 0: `(20, 9)`
- index 1: `(17, 8)`
- index 2: `(17, 3)`
- index 3: `(10, 5)`

Replacement pair:
- `(17, 4)`

At the root, children are `(17, 8)` and `(17, 3)`. Because values tie, the larger child is `(17, 8)` due to larger `id`. Since `(17, 4)` is not greater than `(17, 8)`, `(17, 8)` moves to index 0. The replacement pair is then placed at index 1 because its only child `(10, 5)` is smaller. Output heap:
- index 0: `(17, 8)`
- index 1: `(17, 4)`
- index 2: `(17, 3)`
- index 3: `(10, 5)`

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state:
- none

Output ports after reset and until the first accepted input/output transaction:
- `out_valid = 0`
- `out_bh_val = 0`
- `out_bh_ids = 0`

`in_ready` may be asserted whenever the module can accept a new transaction. A stateless one-transaction pipeline is permitted.
