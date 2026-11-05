module data_systems_bitmap_roaring_bitset_intersect_16w (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a0,
    input  logic [63:0]  in_a1,
    input  logic [63:0]  in_a2,
    input  logic [63:0]  in_a3,
    input  logic [63:0]  in_a4,
    input  logic [63:0]  in_a5,
    input  logic [63:0]  in_a6,
    input  logic [63:0]  in_a7,
    input  logic [63:0]  in_a8,
    input  logic [63:0]  in_a9,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a14,
    input  logic [63:0]  in_a15,
    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,
    input  logic [63:0]  in_b4,
    input  logic [63:0]  in_b5,
    input  logic [63:0]  in_b6,
    input  logic [63:0]  in_b7,
    input  logic [63:0]  in_b8,
    input  logic [63:0]  in_b9,
    input  logic [63:0]  in_b10,
    input  logic [63:0]  in_b11,
    input  logic [63:0]  in_b12,
    input  logic [63:0]  in_b13,
    input  logic [63:0]  in_b14,
    input  logic [63:0]  in_b15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_w0,
    output logic [63:0]  out_w1,
    output logic [63:0]  out_w2,
    output logic [63:0]  out_w3,
    output logic [63:0]  out_w4,
    output logic [63:0]  out_w5,
    output logic [63:0]  out_w6,
    output logic [63:0]  out_w7,
    output logic [63:0]  out_w8,
    output logic [63:0]  out_w9,
    output logic [63:0]  out_w10,
    output logic [63:0]  out_w11,
    output logic [63:0]  out_w12,
    output logic [63:0]  out_w13,
    output logic [63:0]  out_w14,
    output logic [63:0]  out_w15,
    output logic [10:0]  out_cardinality,
    output logic         out_intersects
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    function automatic [6:0] popcount64(input logic [63:0] x);
        integer i;
        begin
            popcount64 = 7'd0;
            for (i = 0; i < 64; i = i + 1) begin
                popcount64 = popcount64 + x[i];
            end
        end
    endfunction

    logic [63:0] and0, and1, and2, and3, and4, and5, and6, and7;
    logic [63:0] and8, and9, and10, and11, and12, and13, and14, and15;
    logic [6:0] pc0, pc1, pc2, pc3, pc4, pc5, pc6, pc7;
    logic [6:0] pc8, pc9, pc10, pc11, pc12, pc13, pc14, pc15;
    logic [10:0] cardinality_next;
    logic intersects_next;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        and0  = in_a0  & in_b0;
        and1  = in_a1  & in_b1;
        and2  = in_a2  & in_b2;
        and3  = in_a3  & in_b3;
        and4  = in_a4  & in_b4;
        and5  = in_a5  & in_b5;
        and6  = in_a6  & in_b6;
        and7  = in_a7  & in_b7;
        and8  = in_a8  & in_b8;
        and9  = in_a9  & in_b9;
        and10 = in_a10 & in_b10;
        and11 = in_a11 & in_b11;
        and12 = in_a12 & in_b12;
        and13 = in_a13 & in_b13;
        and14 = in_a14 & in_b14;
        and15 = in_a15 & in_b15;

        pc0  = popcount64(and0);
        pc1  = popcount64(and1);
        pc2  = popcount64(and2);
        pc3  = popcount64(and3);
        pc4  = popcount64(and4);
        pc5  = popcount64(and5);
        pc6  = popcount64(and6);
        pc7  = popcount64(and7);
        pc8  = popcount64(and8);
        pc9  = popcount64(and9);
        pc10 = popcount64(and10);
        pc11 = popcount64(and11);
        pc12 = popcount64(and12);
        pc13 = popcount64(and13);
        pc14 = popcount64(and14);
        pc15 = popcount64(and15);

        cardinality_next = pc0 + pc1 + pc2 + pc3 +
                           pc4 + pc5 + pc6 + pc7 +
                           pc8 + pc9 + pc10 + pc11 +
                           pc12 + pc13 + pc14 + pc15;

        intersects_next = (cardinality_next != 11'd0);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy            <= 1'b0;
            out_w0          <= 64'h0000000000000000;
            out_w1          <= 64'h0000000000000000;
            out_w2          <= 64'h0000000000000000;
            out_w3          <= 64'h0000000000000000;
            out_w4          <= 64'h0000000000000000;
            out_w5          <= 64'h0000000000000000;
            out_w6          <= 64'h0000000000000000;
            out_w7          <= 64'h0000000000000000;
            out_w8          <= 64'h0000000000000000;
            out_w9          <= 64'h0000000000000000;
            out_w10         <= 64'h0000000000000000;
            out_w11         <= 64'h0000000000000000;
            out_w12         <= 64'h0000000000000000;
            out_w13         <= 64'h0000000000000000;
            out_w14         <= 64'h0000000000000000;
            out_w15         <= 64'h0000000000000000;
            out_cardinality <= 11'h000;
            out_intersects  <= 1'b0;
        end else if (input_accepted) begin
            out_w0          <= and0;
            out_w1          <= and1;
            out_w2          <= and2;
            out_w3          <= and3;
            out_w4          <= and4;
            out_w5          <= and5;
            out_w6          <= and6;
            out_w7          <= and7;
            out_w8          <= and8;
            out_w9          <= and9;
            out_w10         <= and10;
            out_w11         <= and11;
            out_w12         <= and12;
            out_w13         <= and13;
            out_w14         <= and14;
            out_w15         <= and15;
            out_cardinality <= cardinality_next;
            out_intersects  <= intersects_next;
            busy            <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule