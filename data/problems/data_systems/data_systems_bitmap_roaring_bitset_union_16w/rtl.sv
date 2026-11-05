module data_systems_bitmap_roaring_bitset_union_16w (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a_w0,
    input  logic [63:0]  in_a_w1,
    input  logic [63:0]  in_a_w2,
    input  logic [63:0]  in_a_w3,
    input  logic [63:0]  in_a_w4,
    input  logic [63:0]  in_a_w5,
    input  logic [63:0]  in_a_w6,
    input  logic [63:0]  in_a_w7,
    input  logic [63:0]  in_a_w8,
    input  logic [63:0]  in_a_w9,
    input  logic [63:0]  in_a_w10,
    input  logic [63:0]  in_a_w11,
    input  logic [63:0]  in_a_w12,
    input  logic [63:0]  in_a_w13,
    input  logic [63:0]  in_a_w14,
    input  logic [63:0]  in_a_w15,
    input  logic [63:0]  in_b_w0,
    input  logic [63:0]  in_b_w1,
    input  logic [63:0]  in_b_w2,
    input  logic [63:0]  in_b_w3,
    input  logic [63:0]  in_b_w4,
    input  logic [63:0]  in_b_w5,
    input  logic [63:0]  in_b_w6,
    input  logic [63:0]  in_b_w7,
    input  logic [63:0]  in_b_w8,
    input  logic [63:0]  in_b_w9,
    input  logic [63:0]  in_b_w10,
    input  logic [63:0]  in_b_w11,
    input  logic [63:0]  in_b_w12,
    input  logic [63:0]  in_b_w13,
    input  logic [63:0]  in_b_w14,
    input  logic [63:0]  in_b_w15,

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
    output logic [10:0]  out_cardinality
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic [6:0] popcount64(input logic [63:0] value);
    integer idx;
    begin
        popcount64 = 7'd0;
        for (idx = 0; idx < 64; idx = idx + 1) begin
            popcount64 = popcount64 + {6'd0, value[idx]};
        end
    end
endfunction

logic [63:0] or_w0;
logic [63:0] or_w1;
logic [63:0] or_w2;
logic [63:0] or_w3;
logic [63:0] or_w4;
logic [63:0] or_w5;
logic [63:0] or_w6;
logic [63:0] or_w7;
logic [63:0] or_w8;
logic [63:0] or_w9;
logic [63:0] or_w10;
logic [63:0] or_w11;
logic [63:0] or_w12;
logic [63:0] or_w13;
logic [63:0] or_w14;
logic [63:0] or_w15;

logic [6:0] pc0;
logic [6:0] pc1;
logic [6:0] pc2;
logic [6:0] pc3;
logic [6:0] pc4;
logic [6:0] pc5;
logic [6:0] pc6;
logic [6:0] pc7;
logic [6:0] pc8;
logic [6:0] pc9;
logic [6:0] pc10;
logic [6:0] pc11;
logic [6:0] pc12;
logic [6:0] pc13;
logic [6:0] pc14;
logic [6:0] pc15;

logic [10:0] card_sum;

always_comb begin
    or_w0  = in_a_w0  | in_b_w0;
    or_w1  = in_a_w1  | in_b_w1;
    or_w2  = in_a_w2  | in_b_w2;
    or_w3  = in_a_w3  | in_b_w3;
    or_w4  = in_a_w4  | in_b_w4;
    or_w5  = in_a_w5  | in_b_w5;
    or_w6  = in_a_w6  | in_b_w6;
    or_w7  = in_a_w7  | in_b_w7;
    or_w8  = in_a_w8  | in_b_w8;
    or_w9  = in_a_w9  | in_b_w9;
    or_w10 = in_a_w10 | in_b_w10;
    or_w11 = in_a_w11 | in_b_w11;
    or_w12 = in_a_w12 | in_b_w12;
    or_w13 = in_a_w13 | in_b_w13;
    or_w14 = in_a_w14 | in_b_w14;
    or_w15 = in_a_w15 | in_b_w15;

    pc0  = popcount64(or_w0);
    pc1  = popcount64(or_w1);
    pc2  = popcount64(or_w2);
    pc3  = popcount64(or_w3);
    pc4  = popcount64(or_w4);
    pc5  = popcount64(or_w5);
    pc6  = popcount64(or_w6);
    pc7  = popcount64(or_w7);
    pc8  = popcount64(or_w8);
    pc9  = popcount64(or_w9);
    pc10 = popcount64(or_w10);
    pc11 = popcount64(or_w11);
    pc12 = popcount64(or_w12);
    pc13 = popcount64(or_w13);
    pc14 = popcount64(or_w14);
    pc15 = popcount64(or_w15);

    card_sum = pc0 + pc1 + pc2 + pc3 + pc4 + pc5 + pc6 + pc7 +
               pc8 + pc9 + pc10 + pc11 + pc12 + pc13 + pc14 + pc15;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy            <= 1'b0;
        out_w0          <= 64'd0;
        out_w1          <= 64'd0;
        out_w2          <= 64'd0;
        out_w3          <= 64'd0;
        out_w4          <= 64'd0;
        out_w5          <= 64'd0;
        out_w6          <= 64'd0;
        out_w7          <= 64'd0;
        out_w8          <= 64'd0;
        out_w9          <= 64'd0;
        out_w10         <= 64'd0;
        out_w11         <= 64'd0;
        out_w12         <= 64'd0;
        out_w13         <= 64'd0;
        out_w14         <= 64'd0;
        out_w15         <= 64'd0;
        out_cardinality <= 11'd0;
    end else if (input_accepted) begin
        out_w0          <= or_w0;
        out_w1          <= or_w1;
        out_w2          <= or_w2;
        out_w3          <= or_w3;
        out_w4          <= or_w4;
        out_w5          <= or_w5;
        out_w6          <= or_w6;
        out_w7          <= or_w7;
        out_w8          <= or_w8;
        out_w9          <= or_w9;
        out_w10         <= or_w10;
        out_w11         <= or_w11;
        out_w12         <= or_w12;
        out_w13         <= or_w13;
        out_w14         <= or_w14;
        out_w15         <= or_w15;
        out_cardinality <= card_sum;
        busy            <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule