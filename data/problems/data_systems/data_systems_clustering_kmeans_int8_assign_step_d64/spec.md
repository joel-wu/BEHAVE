# INT8 Nearest-Centroid Assignment, Eight Centers in 64 Dimensions

Compute exact squared Euclidean distances from one signed-INT8 vector to eight
supplied signed-INT8 centroids. Return the closest centroid and its distance.
All centroid values are supplied explicitly.

## Interface

### RTL interface

```systemverilog
module data_systems_clustering_kmeans_int8_assign_step_d64 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_vector,
    input logic [4095:0] in_centroids,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_assign,
    output logic [21:0] out_distance
);
endmodule
```

### Behavior interface

```python
class KMeansInt8AssignStepD64:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_vector, in_centroids): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_assign": ..., "out_distance": ...}`. Both fields are Python integers
holding the specified output port bit patterns. Packed inputs are also
integers, not lists of lanes or centroids.

## Modules

The declared top-level module implements one complete eight-center assignment.

## Functional Behavior

Vector lane j is `in_vector[8*j +: 8]`. Centroid c, lane j is
`in_centroids[8*(64*c+j) +: 8]`, for c=0..7 and j=0..63. Every lane is signed
two's-complement in [-128,127]; all physical words are legal. Both packed
operands belong to the accepted request and may change on the next request.

For each centroid c, compute D_c=sum_j (x_j-center_cj)^2 exactly, without wrap,
rounding or saturation. Return the smallest index attaining min_c D_c as
`out_assign` and its D_c as `out_distance`. A difference is in [-255,255] and
D_c<=64*255^2=4161600<2^22, so the distance port represents every result exactly.
Duplicate centroids are legal and use the same lowest-index tie rule.

This retains the nearest-centroid assignment used by Faiss Clustering.cpp's
`index.add`/`index.search(...,1,...)` iteration, not centroid training or arbitrary
Index implementations. Eight centers, exact signed-INT8 distances and explicit
per-request centroids specialize that concept. Interpreting these integer lanes
as real values introduces no quantization error; converting arbitrary float
centroids into these lanes is the caller's separate operation. No claim is made
about Faiss backend accumulation order or approximate-search configurations.

## Reset Behavior

Reset is synchronous active-high. It clears pending responses and output data;
there is no persistent centroid memory. No input is accepted while reset is
asserted. Each accepted request produces one ordered response unless reset
interrupts it. Valid and payload remain stable under backpressure, and input
and output may transfer together. Internal latency is not prescribed.
